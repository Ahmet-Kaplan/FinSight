import asyncio
import io
import logging
import sys
import os
import dill  # Use dill instead of pickle for more robust serialization
import traceback
import uuid
import inspect
import importlib
import types
from contextlib import redirect_stdout, redirect_stderr
from typing import Dict, Any, List, Tuple
import pandas as pd

_sandbox_logger = logging.getLogger(__name__ + ".sandbox")

# Modules that LLM-generated code is NOT allowed to import.
RESTRICTED_MODULES = frozenset({
    "subprocess", "shutil", "ctypes", "signal", "multiprocessing",
    "socket", "http.server", "xmlrpc", "ftplib", "smtplib",
    "webbrowser", "code", "codeop", "compileall",
})

# Default execution timeout in seconds.
DEFAULT_EXEC_TIMEOUT = 120


class AsyncCodeExecutor:
    """
    Lightweight Python sandbox capable of executing LLM-generated code.
    """
    def __init__(self, working_dir: str, exec_timeout: float = DEFAULT_EXEC_TIMEOUT,
                 language: str = 'en'):
        self.working_dir = working_dir
        self.exec_timeout = exec_timeout
        self.language = language
        os.makedirs(self.working_dir, exist_ok=True)
        self.session_id = str(uuid.uuid4())
        self.globals: Dict[str, Any] = self.create_clean_globals()

    def create_clean_globals(self) -> Dict[str, Any]:
        """
        Create a global namespace populated with built-ins and pre-imported libraries.

        Security hardening:
        - ``__import__`` is wrapped to block RESTRICTED_MODULES.
        - ``open()`` is wrapped to block writes outside ``self.working_dir``.
        """
        # Copy builtins so we can patch __import__ and open without
        # affecting the host process.
        import builtins as _builtins_mod
        safe_builtins = dict(vars(_builtins_mod))

        _original_import = _builtins_mod.__import__
        _safe_os_holder: Dict[str, Any] = {"os": None}

        def _restricted_import(name, *args, **kwargs):
            top_level = name.split(".")[0]
            if top_level in RESTRICTED_MODULES:
                _sandbox_logger.warning(
                    "Blocked import of restricted module '%s' in code sandbox", name,
                )
                raise ImportError(
                    f"Importing '{name}' is not allowed in the code sandbox."
                )
            if top_level == "os" and _safe_os_holder.get("os") is not None:
                return _safe_os_holder["os"]
            return _original_import(name, *args, **kwargs)

        safe_builtins["__import__"] = _restricted_import

        # Restrict open() writes to working_dir
        import os as _os_mod
        _original_open = _builtins_mod.open
        _allowed_dir = _os_mod.path.abspath(self.working_dir)
        _agent_dir = _os_mod.path.abspath(_os_mod.path.join(_allowed_dir, _os_mod.pardir))
        _run_working_dir = _os_mod.path.abspath(_os_mod.path.join(_agent_dir, _os_mod.pardir, _os_mod.pardir))
        _state_dir = _os_mod.path.join(_run_working_dir, "state")
        _memory_dir = _os_mod.path.join(_run_working_dir, "memory")
        _agent_working_dir = _os_mod.path.join(_run_working_dir, "agent_working")
        _allowed_roots = [_allowed_dir, _state_dir, _memory_dir, _agent_working_dir]
        _allowed_root_artifact_exts = {".md", ".docx", ".pdf", ".png"}

        def _is_under(path: str, root: str) -> bool:
            try:
                path_abs = _os_mod.path.abspath(path)
                root_abs = _os_mod.path.abspath(root)
                return path_abs == root_abs or path_abs.startswith(root_abs + _os_mod.sep)
            except Exception:
                return False

        def _is_allowed_mutation_path(path: str, *, directory: bool = False) -> bool:
            abs_path = _os_mod.path.abspath(str(path))
            # Always allow mutations inside scoped roots.
            for root in _allowed_roots:
                if _is_under(abs_path, root):
                    return True
            # Allow report artifact writes only at run working dir root.
            if not directory and _os_mod.path.dirname(abs_path) == _run_working_dir:
                ext = _os_mod.path.splitext(abs_path)[1].lower()
                if ext in _allowed_root_artifact_exts:
                    return True
            return False

        def _assert_allowed(path: str, *, op_name: str, directory: bool = False) -> str:
            abs_path = _os_mod.path.abspath(str(path))
            if not _is_allowed_mutation_path(abs_path, directory=directory):
                _sandbox_logger.warning(
                    "Blocked os mutation '%s' on '%s' (outside sandbox roots).",
                    op_name,
                    path,
                )
                raise PermissionError(
                    f"Operation '{op_name}' is not allowed on '{path}'. "
                    f"Sandbox writes are restricted to {_allowed_roots} and "
                    f"top-level report artifacts under {_run_working_dir}."
                )
            return abs_path

        def _restricted_open(file, mode="r", *args, **kwargs):
            if any(m in mode for m in ("w", "a", "x", "+")):
                _assert_allowed(str(file), op_name="open", directory=False)
            return _original_open(file, mode, *args, **kwargs)

        safe_builtins["open"] = _restricted_open

        class _SafeOSProxy:
            path = _os_mod.path
            sep = _os_mod.sep
            altsep = _os_mod.altsep
            linesep = _os_mod.linesep
            environ = _os_mod.environ

            def __getattr__(self, name: str):
                return getattr(_os_mod, name)

        safe_os = _SafeOSProxy()

        def _safe_remove(path, *args, **kwargs):
            _assert_allowed(path, op_name="remove")
            return _os_mod.remove(path, *args, **kwargs)

        def _safe_unlink(path, *args, **kwargs):
            _assert_allowed(path, op_name="unlink")
            return _os_mod.unlink(path, *args, **kwargs)

        def _safe_rename(src, dst, *args, **kwargs):
            _assert_allowed(src, op_name="rename-src")
            _assert_allowed(dst, op_name="rename-dst")
            return _os_mod.rename(src, dst, *args, **kwargs)

        def _safe_replace(src, dst, *args, **kwargs):
            _assert_allowed(src, op_name="replace-src")
            _assert_allowed(dst, op_name="replace-dst")
            return _os_mod.replace(src, dst, *args, **kwargs)

        def _safe_rmdir(path, *args, **kwargs):
            _assert_allowed(path, op_name="rmdir", directory=True)
            return _os_mod.rmdir(path, *args, **kwargs)

        def _safe_mkdir(path, *args, **kwargs):
            _assert_allowed(path, op_name="mkdir", directory=True)
            return _os_mod.mkdir(path, *args, **kwargs)

        def _safe_makedirs(name, mode=0o777, exist_ok=False):
            _assert_allowed(name, op_name="makedirs", directory=True)
            return _os_mod.makedirs(name, mode=mode, exist_ok=exist_ok)

        safe_os.remove = _safe_remove
        safe_os.unlink = _safe_unlink
        safe_os.rename = _safe_rename
        safe_os.replace = _safe_replace
        safe_os.rmdir = _safe_rmdir
        safe_os.mkdir = _safe_mkdir
        safe_os.makedirs = _safe_makedirs
        _safe_os_holder["os"] = safe_os

        context = {'__builtins__': safe_builtins}

        import os
        import json
        import math
        import re
        import random
        import datetime
        import asyncio
        import io
        import sys
        
        context.update({
            'os': safe_os,
            'json': json,
            'math': math,
            're': re,
            'random': random,
            'datetime': datetime,
            'asyncio': asyncio,
            'io': io,
            'sys': sys
        })

        try:
            import pandas as pd
            import numpy as np
            import matplotlib.pyplot as plt
            import matplotlib
            import matplotlib.font_manager as fm
            # Language-aware font selection:
            # English runs use DejaVu Sans only (no CJK font probing).
            # Chinese runs probe for an available CJK font, with DejaVu fallback.
            from src.utils.chart_utils import detect_available_font
            if self.language == 'zh':
                _safe_font = detect_available_font([
                    'SimHei', 'KaiTi', 'Noto Sans CJK SC', 'PingFang SC',
                    'Arial Unicode MS', 'DejaVu Sans',
                ]) or 'sans-serif'
            else:
                _safe_font = 'DejaVu Sans'
            matplotlib.rcParams['font.sans-serif'] = [_safe_font, 'sans-serif']
            matplotlib.rcParams['axes.unicode_minus'] = False
            context.update({
                'pd': pd,
                'pandas': pd,
                'np': np,
                'numpy': np,
                'plt': plt,
                'matplotlib': matplotlib,
            })
        except ImportError as e:
            print(f"Warning: Failed to pre-import data libraries: {e}")

        return context


    def set_variable(self, name: str, value: Any):
        """
        Inject an external variable or function into the executor's global scope.
        """
        self.globals[name] = value

    def get_variable(self, name: str) -> Any:
        """
        Retrieve a variable from the executor globals.
        """
        return self.globals.get(name)

    def save_state(self) -> bytes:
        """
        Capture a minimal, reconstructable execution state:
        - imports: module names to import upon restore
        - definitions: user-defined functions/classes (store source code)
        - variables: simple serializable variables (skip complex objects when possible)
        """
        state: Dict[str, Any] = {
            'imports': [],
            'definitions': [],  # list of dicts: {name, kind, source}
            'variables': {},    # name -> dill-bytes
        }

        # 1) Track imported modules
        module_names: List[str] = []
        for name, value in list(self.globals.items()):
            if isinstance(value, types.ModuleType):
                if value.__name__ not in ('__builtins__',):
                    module_names.append(value.__name__)
        # Deduplicate/sort for deterministic ordering
        state['imports'] = sorted(set(module_names))

        # 2) Collect user-defined functions/classes (with source)
        def try_collect_definition(obj_name: str, obj: Any, kind: str):
            try:
                # Capture only exec-defined objects (filename '<string>' or module '__main__')
                source = inspect.getsource(obj)
                state['definitions'].append({'name': obj_name, 'kind': kind, 'source': source})
            except Exception:
                # Skip if source retrieval fails
                pass

        for name, value in list(self.globals.items()):
            # Skip special names
            if name.startswith('__') and name.endswith('__'):
                continue
            if inspect.isfunction(value):
                try_collect_definition(name, value, 'function')
            elif inspect.isclass(value):
                try_collect_definition(name, value, 'class')

        # 3) Collect simple variables (using dill when feasible)
        SIMPLE_ALLOWED_TYPES = (int, float, str, bool)
        CONTAINER_TYPES = (list, dict, tuple, set)

        def is_simple(obj: Any, depth: int = 0) -> bool:
            if isinstance(obj, SIMPLE_ALLOWED_TYPES):
                return True
            if isinstance(obj, (pd.DataFrame, )):
                return False
            if isinstance(obj, CONTAINER_TYPES) and depth < 2:
                try:
                    if isinstance(obj, dict):
                        return all(isinstance(k, (str, int)) and is_simple(v, depth + 1) for k, v in obj.items())
                    else:
                        return all(is_simple(v, depth + 1) for v in obj)
                except Exception:
                    return False
            return False

        for name, value in list(self.globals.items()):
            if name in ('__builtins__',):
                continue
            if inspect.isfunction(value) or inspect.isclass(value) or isinstance(value, types.ModuleType):
                continue
            if name.startswith('_'):
                continue
            # Prefer storing simple variables; use dill cautiously for complex ones
            to_store = None
            if is_simple(value):
                try:
                    to_store = dill.dumps(value)
                except Exception:
                    to_store = None
            else:
                # Attempt to serialize complex objects; skip if it fails
                try:
                    to_store = dill.dumps(value)
                except Exception:
                    to_store = None
            if to_store is not None:
                state['variables'][name] = to_store

        try:
            return dill.dumps(state)
        except Exception as e:
            print(f"[{self.session_id}] Warning: failed to save lightweight state: {e}")
            return dill.dumps({'imports': [], 'definitions': [], 'variables': {}})

    def load_state(self, state: bytes):
        """
        Restore the lightweight state by re-importing modules, recreating definitions, and loading variables.
        """
        try:
            payload = dill.loads(state)
        except Exception as e:
            print(f"[{self.session_id}] Error: failed to load state: {e}. Resetting to a clean environment.")
            self.globals = self.create_clean_globals()
            return

        self.globals = self.create_clean_globals()

        # 1) Re-import modules
        for mod_name in payload.get('imports', []) or []:
            try:
                mod = importlib.import_module(mod_name)
                self.globals[mod_name.split('.')[-1]] = mod
            except Exception:
                # Skip modules that fail to import
                continue

        # 2) Recreate function/class definitions
        for item in payload.get('definitions', []) or []:
            source = item.get('source')
            if not source:
                continue
            try:
                exec(source, self.globals)
            except Exception:
                # Skip definitions that require missing modules
                continue

        # 3) Restore variables
        for name, raw in (payload.get('variables', {}) or {}).items():
            try:
                self.globals[name] = dill.loads(raw)
            except Exception:
                # Skip if deserialization fails
                continue
    

    def get_environment_info(self) -> str:
        """
        Summarize the current execution environment for prompt construction.
        """
        info_parts = []
        
        # Capture important data variables
        important_vars = {}
        for var_name, var_value in self.globals.items():
            if not var_name.startswith('_') and var_name not in ['In', 'Out', 'get_ipython', 'exit', 'quit']:
                try:
                    if hasattr(var_value, 'shape'):  # pandas DataFrame, numpy array
                        important_vars[var_name] = f"{type(var_value).__name__} with shape {var_value.shape}"
                    elif var_name in ['session_output_dir']:  # key path variables
                        important_vars[var_name] = str(var_value)
                    elif isinstance(var_value, (int, float, str, bool)) and len(str(var_value)) < 100:
                        important_vars[var_name] = f"{type(var_value).__name__}: {var_value}"
                    elif hasattr(var_value, '__module__') and var_value.__module__ in ['pandas', 'numpy', 'matplotlib.pyplot']:
                        important_vars[var_name] = f"Imported module: {var_value.__module__}"
                    if isinstance(var_value, pd.DataFrame):
                        important_vars[var_name] += ", and dtypes: " + str(var_value.dtypes)
                except:
                    continue
        
        if important_vars:
            info_parts.append("Current environment variables:")
            for var_name, var_info in important_vars.items():
                info_parts.append(f"- {var_name}: {var_info}")
        else:
            info_parts.append("Environment preloads pandas, numpy, matplotlib, and related libraries.")
        
        if 'session_output_dir' in self.globals:
            info_parts.append(f"Image output directory: session_output_dir = '{self.globals['session_output_dir']}'")
        
        return "\n".join(info_parts)

    async def execute(self, code: str) -> dict:
        """
        Execute code asynchronously by delegating to a thread pool so the event loop
        remains responsive. If the code defines `async def async_main():`, run it
        after the initial exec to support awaitable workflows.

        Returns {stdout: str, stderr: str, error: bool}.
        """
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        has_error = False
        # Safe font header — LLM-generated chart code sets the correct font via prompt instructions
        header = "import matplotlib.pyplot as plt; plt.rcParams['axes.unicode_minus'] = False"
        code = header + '\n' + code
        # Wrap exec so it can run inside a thread
        def sync_exec():
            nonlocal has_error
            try:
                # Redirect stdout/stderr
                with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                    # Execute code within the custom global scope
                    exec(code, self.globals)
            except Exception:
                # Capture exec-level exceptions
                has_error = True
                stderr_capture.write(traceback.format_exc())
                print("error code: code = \n", code)

        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, sync_exec),
                timeout=self.exec_timeout,
            )
        except asyncio.TimeoutError:
            has_error = True
            stderr_capture.write(
                f"ExecutionTimeout: code execution exceeded {self.exec_timeout}s limit\n"
            )
        
        # Run user-defined async entry points if present
        if 'async_main' in self.globals and \
           asyncio.iscoroutinefunction(self.globals['async_main']):
            
            try:
                # Redirect stdout/stderr as well
                with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                    # Await the user coroutine
                    await self.globals['async_main']()
            except Exception:
                # Capture async execution errors
                has_error = True
                stderr_capture.write(traceback.format_exc())
            finally:
                # Remove the coroutine to avoid reruns
                del self.globals['async_main']
        
        # Close all matplotlib figures to prevent memory leaks during long runs
        try:
            import matplotlib.pyplot as _plt_cleanup
            _plt_cleanup.close('all')
        except Exception:
            pass

        stdout = stdout_capture.getvalue()
        stderr = stderr_capture.getvalue()
        if stdout == "":
            stdout = 'Run completed with no output.'
        return {
            'stdout': stdout,
            'stderr': stderr,
            'error': has_error
        }
