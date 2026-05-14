"""Tests for chart code runtime validation"""
import ast

def _validate_chart_code(code, known_vars=None):
    """Simplified version of data_analyzer._validate_chart_code for testing."""
    import builtins
    if known_vars is None:
        known_vars = set()

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Syntax error: {e}"

    referenced = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            referenced.add(node.id)

    all_known = known_vars | set(dir(builtins)) | {'pd', 'np', 'plt', 'sns', 'os', 'json', 'math', 're', 'datetime', 'io', 'sys', 'random', 'asyncio'}

    defined_in_code = set()
    for node in ast.walk(tree):
        # Any Name in Store context is a definition (handles tuple unpacking too)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined_in_code.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined_in_code.add(node.name)
            for arg in node.args.args:
                defined_in_code.add(arg.arg)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                defined_in_code.add(alias.asname if alias.asname else alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                defined_in_code.add(alias.asname or alias.name)
        elif isinstance(node, ast.For):
            if isinstance(node.target, ast.Name):
                defined_in_code.add(node.target.id)
            elif isinstance(node.target, ast.Tuple):
                for elt in node.target.elts:
                    if isinstance(elt, ast.Name):
                        defined_in_code.add(elt.id)

    unknown = referenced - all_known - defined_in_code

    suspicious = []
    for node in ast.walk(tree):
        if isinstance(node, ast.List) and len(node.elts) >= 4:
            if all(isinstance(e, ast.Constant) and isinstance(e.value, (int, float)) for e in node.elts):
                suspicious.append(len(node.elts))

    warnings = []
    if unknown:
        warnings.append(f"Undefined: {unknown}")
    if suspicious:
        warnings.append(f"Hardcoded lists: {suspicious}")

    if unknown:
        return False, "; ".join(warnings)
    return True, "; ".join(warnings) if warnings else ""

def test_validate_valid_code():
    code = "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nax.plot([1,2,3])\nplt.savefig('chart.png')"
    valid, msg = _validate_chart_code(code)
    assert valid is True

def test_validate_undefined_var():
    code = "plt.plot(nonexistent_variable)\nplt.savefig('chart.png')"
    valid, msg = _validate_chart_code(code)
    assert valid is False
    assert "nonexistent_variable" in msg

def test_validate_hardcoded_data_warning():
    code = "data = [100, 200, 300, 400, 500]\nimport matplotlib.pyplot as plt\nplt.bar(range(5), data)\nplt.savefig('chart.png')"
    valid, msg = _validate_chart_code(code)
    assert valid is True  # Should pass but warn
    assert "Hardcoded" in msg
