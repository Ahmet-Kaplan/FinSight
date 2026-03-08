import os
import re
import json
import json_repair
import dill
from typing import List, Dict, Any, Tuple
import asyncio
from threading import Semaphore
from src.agents.base_agent import BaseAgent
from src.agents import DeepSearchAgent
from src.tools import ToolResult
from src.utils import IndexBuilder
from src.utils import image_to_base64

# TODO: Break parameter passing into explicit arguments
# TODO: Standardize I/O structures as lightweight classes

class DataAnalyzer(BaseAgent):
    AGENT_NAME = 'data_analyzer'
    AGENT_DESCRIPTION = 'a agent that can analyze data and generate report'
    NECESSARY_KEYS = ['task', 'analysis_task']

    @staticmethod
    def _resolve_prompt_profile(target_type: str) -> str:
        """Map pipeline target_type to available analyzer prompt packs."""
        if not target_type:
            return 'general'
        if target_type in ('general', 'industry'):
            return 'general'
        if 'financial' in target_type or target_type in ('company', 'macro'):
            return 'financial'
        return 'general'

    def __init__(
        self,
        config,
        tools = [],
        use_llm_name: str = "deepseek-chat",
        use_vlm_name: str = "qwen/qwen3-vl-235b-a22b-instruct",
        use_embedding_name: str = 'qwen/qwen3-embedding-0.6b',
        enable_code = True,
        memory = None,
        agent_id: str = None
    ):
        super().__init__(
            config=config,
            tools=tools,
            use_llm_name=use_llm_name,
            enable_code=enable_code,
            memory=memory,
            agent_id=agent_id
        )
        if self.tools == []:
            self._set_default_tools()

        # Load prompts using the new YAML-based loader
        from src.utils.prompt_loader import get_prompt_loader
        target_type = self.config.config['target_type']
        prompt_profile = self._resolve_prompt_profile(target_type)
        self.prompt_loader = get_prompt_loader('data_analyzer', report_type=prompt_profile)
        
        # Store prompts as instance attributes for easy access
        self.DATA_ANALYSIS_PROMPT = self.prompt_loader.get_prompt('data_analysis')
        self.DATA_ANALYSIS_PROMPT_WO_CHART = self.prompt_loader.get_prompt('data_analysis_wo_chart')
        self.DATA_API_PROMPT = self.prompt_loader.get_prompt('data_api')
        self.REPORT_DRAFT_PROMPT = self.prompt_loader.get_prompt('report_draft')
        self.REPORT_DRAFT_PROMPT_WO_CHART = self.prompt_loader.get_prompt('report_draft_wo_chart')
        self.DRAW_CHART_PROMPT = self.prompt_loader.get_prompt('draw_chart')
        self.VLM_CRITIQUE_PROMPT = self.prompt_loader.get_prompt('vlm_critique')

        self.use_vlm_name = use_vlm_name
        self.vlm = self.config.llm_dict[use_vlm_name]
        self.use_embedding_name = use_embedding_name
        self.current_phase = 'phase1'
 
        self.image_save_dir = os.path.join(self.working_dir, "images")
        os.makedirs(self.image_save_dir, exist_ok = True)
    
    def _set_default_tools(self):
        """
        Attach the default tools needed by the analyzer (search agent, etc.).
        """
        tool_list = []
        tool_list.append(DeepSearchAgent(config=self.config, use_llm_name=self.use_llm_name, memory=self.memory))
        for tool in tool_list:
            self.memory.add_dependency(tool.id, self.id)
        self.tools = tool_list

    async def _prepare_executor(self):
        current_task_data = self.current_task_data
        tool_list = self.tools
        collect_data_list = self.memory.get_collect_data()
        def _get_existed_data(data_id: int):
            return collect_data_list[data_id].data
        def _get_deepsearch_result(query: str):
            ds_agent = tool_list[0]
            output =  asyncio.run(ds_agent.async_run(input_data={
                'task': current_task_data['task'],
                'query': query
            }))
            output = output['final_result']
            return output
        
        self.code_executor.set_variable("session_output_dir", self.image_save_dir)
        self.code_executor.set_variable("collect_data_list", [item.data for item in collect_data_list])
        self.code_executor.set_variable("get_data_from_deep_search", _get_deepsearch_result)
        self.code_executor.set_variable("get_existed_data", _get_existed_data)

        custom_palette = [
            "#8B0000",  # deep crimson
            "#FF2A2A",  # bright red
            "#FF6A4D",  # orange-red
            "#FFDAB9",  # pale peach
            "#FFF5E6",  # cream
            "#FFE4B5",  # beige
            "#A0522D",  # sienna
            "#5C2E1F",  # dark brown
        ]
        self.code_executor.set_variable("custom_palette", custom_palette)
        await self.code_executor.execute("import seaborn as sns\nsns.set_palette(custom_palette)")
    
    async def _prepare_init_prompt(self, input_data: dict) -> list[dict]:
        task = input_data['task']
        enable_chart = input_data['enable_chart']
        handoff_bundle = input_data.get('handoff_bundle')
        collect_data_list = self.memory.get_collect_data(exclude_type=['search', 'click'])
        analysis_task = f"Global Research Objective: {task}\n\nAnalysis Task: {input_data['analysis_task']}"
        data_info = await self._format_collect_data(analysis_task, collect_data_list)
        if handoff_bundle:
            data_info += (
                "\n\n## Resume Handoff Context\n"
                "Continue from these prior findings/failed paths to avoid duplicate work:\n"
                f"{handoff_bundle}\n"
            )

        # Get target language from config
        target_language = self.config.config.get('language', 'zh')
        # Convert language code to full name for clarity in prompt
        language_mapping = {
            'zh': 'Chinese (中文)',
            'en': 'English'
        }
        target_language_name = language_mapping.get(target_language, target_language)

        if enable_chart:
            prompt = self.DATA_ANALYSIS_PROMPT.format(
                api_descriptions=self.DATA_API_PROMPT,
                data_info=data_info,
                current_time=self.current_time,
                user_query=analysis_task,
                target_language=target_language_name
            )
        else:
            prompt = self.DATA_ANALYSIS_PROMPT_WO_CHART.format(
                api_descriptions=self.DATA_API_PROMPT,
                data_info=data_info,
                current_time=self.current_time,
                user_query=analysis_task,
                target_language=target_language_name
            )
        return [{"role": "user", "content": prompt}]
    
    async def _format_collect_data(self, analysis_task, collect_data_list):
        """
        Format collected datasets into a readable string for the prompt.
        """
        # search_result = await self.memory.retrieve_relevant_data(analysis_task, top_k=10, embedding_model=self.use_embedding_name)
        # formatted_data = ""
        # if len(search_result) > 0:
        #     for idx,item in enumerate(search_result):
        #         formatted_data += f"Data (id:{idx}):\n{collect_data_list[idx].brief_str()}\n\n"
        # else:
        #     for idx,item in enumerate(collect_data_list):
        #         formatted_data += f"Data (id:{idx}):\n{item.brief_str()}\n\n"

        formatted_data = ""
        for idx,item in enumerate(collect_data_list):
            formatted_data += f"Data (id:{idx}):\n{item.brief_str()}\n\n"
            
        return formatted_data
    
    async def _handle_report_action(self, action_content: str):
        """Handle a 'final' action from the LLM."""
        return {
            "action": "final_report",
            "action_content": action_content,
            "result": action_content,
            "continue": False,
        }
    
    async def _handle_max_round(self, conversation_history):
        conversation_history = [item["content"] for item in conversation_history]
        analysis_info = "\n\n".join(conversation_history)
        
        # Get target language from config
        target_language = self.config.config.get('language', 'zh')
        language_mapping = {
            'zh': 'Chinese (中文)',
            'en': 'English'
        }
        target_language_name = language_mapping.get(target_language, target_language)
        
        if self.enable_chart:
            prompt = self.REPORT_DRAFT_PROMPT.format(
                current_time = self.current_time,
                analysis_info = analysis_info,
                target_language = target_language_name
            )
        else:
            prompt = self.REPORT_DRAFT_PROMPT_WO_CHART.format(
                current_time = self.current_time,
                analysis_info = analysis_info,
                target_language = target_language_name
            )
        response = await self.llm.generate(
            messages = [
                {"role": "user", "content": prompt}
            ],
            response_format = {"type": "json_object"}
        )
        match = re.search(r'```json([\s\S]*?)```', response)
        if match:
            response = match.group(1).strip()
        try:
            report = json_repair.loads(response)
            report_title = report["title"]
            report_content = report["content"]
            final_result = f'# {report_title}\n{report_content}'
        except Exception:
            final_result = response
        return {'coversation_history': conversation_history, 'final_result': final_result}
    
    def _parse_generated_report(self, response: str):
        basic_task = self.current_task_data['task']
        analysis_task = self.current_task_data['analysis_task']
        report_content = response
        report_title = f"{analysis_task}"

        try:
            split_report_content = report_content.split("\n")
            for idx, line in enumerate(split_report_content):
                if idx > 5:
                    continue
                if line.startswith("#"):
                    report_title = line.strip("#")
                    break
        except Exception:
            pass
        return report_title, report_content
    
    async def _draw_chart(self, input_data, run_data: dict, max_iterations: int = 3):
        report_content = run_data["report_content"]
        analysis_task = input_data['analysis_task']
        chart_names = re.findall(r'@import\s+"(.*?)"', report_content)
        current_variables = self.code_executor.get_environment_info()
        
        name_mapping = {}  # long chart name -> short filename
        name_description_mapping = {}  # long chart name -> description
        chart_code_mapping = {}  # long chart name -> code snippet
        
        # Concurrency control semaphore
        charts_completed = set()
        # Load chart-stage checkpoint if available
        charts_ckpt = await self.load(checkpoint_name='charts.pkl')
        if charts_ckpt is not None:
            charts_state = charts_ckpt.get('charts_state', {})
            charts_completed = set(charts_state.get('completed', []))
            name_mapping.update(charts_state.get('name_mapping', {}))
            name_description_mapping.update(charts_state.get('name_description_mapping', {}))
            chart_code_mapping.update(charts_state.get('chart_code_mapping', {}))

        for long_chart_name in chart_names:
            if long_chart_name in charts_completed:
                continue
            # TODO: Shared environments need isolation; temporarily limit concurrency to 1
            with Semaphore(1):
                new_chart_code, new_chart_name = await self._draw_single_chart(
                    task = analysis_task,
                    report_content = report_content,
                    chart_name = long_chart_name,
                    current_variables = current_variables, 
                    max_iterations = max_iterations
                )
                name_mapping[long_chart_name] = new_chart_name
                chart_code_mapping[long_chart_name] = new_chart_code
                charts_completed.add(long_chart_name)
                # Save progress after each completed chart (chart-specific checkpoint)
                await self.save(
                    state={
                        'charts_state': {
                            'completed': list(charts_completed),
                            'name_mapping': name_mapping,
                            'name_description_mapping': name_description_mapping,
                            'chart_code_mapping': chart_code_mapping,
                        }
                    },
                    checkpoint_name='charts.pkl',
                )
        
        for long_chart_name, new_chart_name in name_mapping.items():
            chart_des = await self._generate_description(new_chart_name)
            name_description_mapping[long_chart_name] = chart_des
            # Persist updated description mapping
            await self.save(
                state={
                    'charts_state': {
                        'completed': list(charts_completed),
                        'name_mapping': name_mapping,
                        'name_description_mapping': name_description_mapping,
                        'chart_code_mapping': chart_code_mapping,
                    }
                },
                checkpoint_name='charts.pkl',
            )

        return chart_code_mapping, name_mapping, name_description_mapping
    
    
    async def _generate_description(self, chart_name: str) -> str:
        chart_name_path = os.path.join(self.image_save_dir, chart_name)
        image_b64 = image_to_base64(chart_name_path)
        if not image_b64:
            return ""
        
        lang = self.config.config.get('language', 'en')
        from src.utils.language_utils import get_language_display_name
        lang_name = get_language_display_name(lang)
        desc_prompt = f"Give a short description in {lang_name} as the caption of this chart, explaining the key data points and takeaways. Your response should be less than 100 words."

        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": desc_prompt + " Don't output any other words."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}}
            ]}
        ]
        response = await self.vlm.generate(
            messages = messages
        )
        return response
    

    async def _draw_single_chart(
        self, 
        task: str,
        report_content: str,
        chart_name: str, 
        current_variables: str,
        max_iterations: int = 3
    ) -> str:
        """
        Run iterative “code generation → VLM critique” cycles for a single chart.
        """
        
        from src.utils.language_utils import get_language_display_name, get_chart_font_for_language, get_chart_label_language_instruction
        lang = self.config.config.get('language', 'en')
        chart_font = get_chart_font_for_language(lang)
        label_instruction = get_chart_label_language_instruction(lang)

        init_prompt = self.DRAW_CHART_PROMPT.format(
            task=task,
            content=report_content,
            chart_name=chart_name,
            data=current_variables,
            target_language=get_language_display_name(lang),
            chart_font=chart_font,
            label_language_instruction=label_instruction
        )
        
        conversation_history = [
            {"role": "user", "content": init_prompt}
        ]
        
        last_successful_code = ""
        last_successful_chart_path = ""
        self.logger.info(f"Start drawing chart: {chart_name}")
        
        # --- Main VLM evaluation loop ---
        for iteration in range(max_iterations):
            self.logger.info(f"Iteration {iteration + 1}")
            
            # --- Phase 1: generate/execute code (up to 3 retries) ---
            chart_code, chart_filepath = await self._generate_and_execute_code(
                conversation_history
            )
            self.logger.info(f"chart_code: {chart_code}")
            self.logger.info(f"chart_filepath: {chart_filepath}")
            if not chart_filepath:
                return last_successful_code, os.path.basename(last_successful_chart_path) if last_successful_chart_path else ""
            self.logger.info("Image generation succeeded")
            last_successful_code = chart_code
            last_successful_chart_path = chart_filepath

            # --- Phase 2: VLM evaluation ---
            image_b64 = image_to_base64(chart_filepath)
            if not image_b64:
                return last_successful_code, os.path.basename(last_successful_chart_path)
            critic_response = await self.vlm.generate(
                messages=[
                    {
                        "role": "user", 
                        "content": [
                            {"type": "text", "text": self.VLM_CRITIQUE_PROMPT.format(
                                task=task,
                                content=report_content,
                            )},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}}
                        ]
                    }
                ]
            )
            self.logger.info("Image critic succeeded")
            if 'finish' in critic_response.lower():
                return last_successful_code, os.path.basename(last_successful_chart_path)
            
            conversation_history.append({"role": "assistant", "content": last_successful_code})
            feedback_for_llm = (
                "The chart above was produced from your previous code. A visualization expert shared the critique below:\n\n"
                f"{critic_response}\n\n"
                "Please write new Python code to address every issue and generate an improved chart. "
                f"Overwrite the previous file '{os.path.basename(last_successful_chart_path)}'."
            )
            conversation_history.append({"role": "user", "content": feedback_for_llm})

        return last_successful_code, os.path.basename(last_successful_chart_path)


    def _validate_chart_code(self, code: str) -> tuple[bool, str]:
        """
        Validate generated chart code before execution.

        Checks:
          1. Syntax correctness via ast.parse().
          2. Undefined variable references (names read but never defined).
          3. Suspicious hardcoded numeric lists (length >= 4).

        Returns:
            (is_valid, message) — *is_valid* is False when the code references
            undefined variables; *message* may contain warnings even when valid.
        """
        import ast
        import builtins

        # 1. Syntax check
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return False, f"SyntaxError: {exc}"

        # 2. Collect names that are *read* (Load context)
        loaded_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                loaded_names.add(node.id)

        # 3. Collect names *defined* within the code
        defined_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                defined_names.add(node.id)
            elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                defined_names.add(node.name)
                # Function arguments count as definitions too
                for arg in node.args.args:
                    defined_names.add(arg.arg)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    defined_names.add(alias.asname if alias.asname else alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    defined_names.add(alias.asname if alias.asname else alias.name)
            elif isinstance(node, ast.For):
                # for-loop target variables
                if isinstance(node.target, ast.Name):
                    defined_names.add(node.target.id)
                elif isinstance(node.target, ast.Tuple):
                    for elt in node.target.elts:
                        if isinstance(elt, ast.Name):
                            defined_names.add(elt.id)

        # Known environment: executor globals + builtins + common aliases
        env_names: set[str] = set(self.code_executor.globals.keys())
        builtin_names: set[str] = set(dir(builtins))
        common_aliases: set[str] = {
            'plt', 'pd', 'np', 'sns', 'os', 'json', 're', 'math',
            'datetime', 'matplotlib', 'seaborn', 'scipy', 'mpl',
        }
        known_names = env_names | builtin_names | common_aliases | defined_names

        # Undefined references
        undefined = loaded_names - known_names
        if undefined:
            return False, f"Undefined variables: {', '.join(sorted(undefined))}"

        # 5. Warn on suspicious hardcoded numeric lists (length >= 4)
        warnings: list[str] = []
        ast_num_cls = getattr(ast, "Num", None)  # Removed in newer Python versions.
        for node in ast.walk(tree):
            if isinstance(node, ast.List) and len(node.elts) >= 4:
                all_literal_nodes = True
                all_numeric = True
                for elt in node.elts:
                    if isinstance(elt, ast.Constant):
                        if not isinstance(elt.value, (int, float)):
                            all_numeric = False
                            break
                    elif ast_num_cls is not None and isinstance(elt, ast_num_cls):
                        # Legacy numeric literal node (older Python versions)
                        continue
                    else:
                        all_literal_nodes = False
                        break
                if all_literal_nodes and all_numeric:
                    warnings.append(
                        f"Suspicious hardcoded numeric list of length {len(node.elts)} at line {node.lineno} "
                        "— ensure data comes from the provided datasets, not fabricated values."
                    )

        return True, "; ".join(warnings)

    async def _generate_and_execute_code(self, conversation_history: list) -> tuple[str | None, str | None]:
        """
        Attempt (up to three times) to generate and execute the chart code.

        Returns:
            (llm_response, chart_filepath) on success; otherwise (None, None).
        """
        for _ in range(3):  # internal retries
            self.logger.info(f"Generating code, attempt {_ + 1}")
            llm_response = await self.llm.generate(
                messages=conversation_history,
                # stop=['</execute']
            )
            action_type, action_content = self._parse_llm_response(llm_response)
            self.logger.info("######################")
            self.logger.info(f"action_type: {action_type}")
            self.logger.info(f"action_content: {action_content}")

            if action_type != "code":
                conversation_history.append({"role": "assistant", "content": llm_response})
                conversation_history.append({"role": "user", "content": "Your reply did not include a valid <execute> code block. Please provide Python code that draws the chart."})
                continue  # retry

            # Validate chart code before execution
            is_valid, validation_msg = self._validate_chart_code(action_content)
            if not is_valid:
                self.logger.warning(f"Chart code validation failed: {validation_msg}")
                conversation_history.append({"role": "assistant", "content": llm_response})
                conversation_history.append({"role": "user", "content": f"Your code references variables that don't exist: {validation_msg}. Fix it."})
                continue
            if validation_msg:
                self.logger.info(f"Chart code warning: {validation_msg}")

            code_result = await self.code_executor.execute(code=action_content)
            self.logger.info(f"code_result: {code_result}")
            if code_result['error']:
                conversation_history.append({"role": "assistant", "content": llm_response})
                error_feedback = (
                    "Your code failed to execute. Here is the error output:\n\n"
                    f"{code_result['stdout']}\n{code_result['stderr']}\n\nPlease fix the code and try again."
                )
                self.logger.info(error_feedback)
                conversation_history.append({"role": "user", "content": error_feedback})
                continue  # retry
            
            # Ensure the code saved a figure
            chart_filenames = re.findall(r"[\"']([^\"']+\.png)[\"']", action_content)
            if not chart_filenames:
                conversation_history.append({"role": "assistant", "content": llm_response}) 
                feedback = "Your code ran but did not save a PNG. Please add `plt.savefig('filename.png')`."
                self.logger.info(feedback)
                conversation_history.append({"role": "user", "content": feedback})
                continue  # retry

            # Confirm the file exists
            from src.utils.chart_utils import sanitize_chart_filename, contains_cjk
            _lang = self.config.config.get('language', 'en')
            _ascii_only = (_lang != 'zh')
            potential_chart_name = sanitize_chart_filename(
                os.path.basename(chart_filenames[0]), ascii_only=_ascii_only
            )
            # CJK guard for English runs: if LLM generated CJK in code despite instructions
            if _lang != 'zh' and contains_cjk(action_content):
                self.logger.warning(
                    "CJK characters detected in chart code for English run — "
                    "chart may contain non-English labels"
                )
            chart_filepath = os.path.join(self.image_save_dir, potential_chart_name) 

            if not os.path.exists(chart_filepath):
                conversation_history.append({"role": "assistant", "content": llm_response})
                feedback = f"The file '{potential_chart_name}' was not found in the output directory. Please ensure the `plt.savefig()` path is correct."
                self.logger.info(feedback)
                conversation_history.append({"role": "user", "content": feedback})
                continue  # retry
            
            return action_content, chart_filepath

        # Bail out after three failed attempts
        return None, None
    
    def _get_persist_extra_state(self) -> Dict[str, Any]:
        return {'current_phase': self.current_phase}
    def _load_persist_extra_state(self, state: Dict[str, Any]):
        self.current_phase = state.get('current_phase', 'phase1')
        
    async def async_run(
        self, 
        input_data: dict, 
        max_iterations: int = 10,
        stop_words: list[str] = [],
        echo=False,
        resume: bool = True,
        checkpoint_name: str = 'latest.pkl',
        enable_chart: bool = True,
        # stop_words: list[str] = ["</execute>", "</report>"]
    ) -> dict:
        input_data = dict(input_data)
        input_data['max_iterations'] = max_iterations
        input_data['enable_chart'] = enable_chart
        self.enable_chart = enable_chart

        if not resume:
            self.current_phase = 'phase1'

        # Phase 1: conversational analysis (handled by BaseAgent)
        if self.current_phase == 'phase1':
            run_result = await super().async_run(
                input_data=input_data,
                max_iterations=max_iterations,
                stop_words=stop_words,
                echo=echo,
                resume=resume,
                checkpoint_name=checkpoint_name,
            )
            self.current_phase = 'phase2'
            await self.save(state={'finished': False, 'current_phase': self.current_phase, 'phase1_result': run_result}, checkpoint_name=checkpoint_name)
        else:
            run_result = self.current_checkpoint.get('phase1_result', {})
        try:
            final_result = run_result['final_result']
        except:
            self.logger.error(f"final_result: {final_result}")
        # Parse the generated analysis report
        if self.current_phase == 'phase2':
            report_title, report_content = self._parse_generated_report(final_result)
            self.logger.info(f"report_title: {report_title}")
            self.current_phase = 'phase3'
            await self.save(state={'report_title': report_title, 'report_content': report_content, 'current_phase': self.current_phase}, checkpoint_name=checkpoint_name)
        else:
            report_title = self.current_checkpoint.get('report_title', '')
            report_content = self.current_checkpoint.get('report_content', '')
        run_result['report_title'] = report_title
        run_result['report_content'] = report_content


        # Phase 2: draw charts (separate checkpoint charts.pkl)
        if self.current_phase == 'phase3' and enable_chart:
            chart_code_mapping, name_mapping, name_description_mapping = await self._draw_chart(input_data, run_result)
            # Clean up/checkpoint bookkeeping once finished
            self.current_phase = 'phase4'
            await self.save(state={
                'current_phase': self.current_phase, 
                'chart_code_mapping': chart_code_mapping, 
                'chart_name_mapping': name_mapping, 
                'chart_name_description_mapping': name_description_mapping
            }, checkpoint_name=checkpoint_name)
        else:
            self.current_phase = 'phase4'
            chart_code_mapping = self.current_checkpoint.get('chart_code_mapping', {})
            name_mapping = self.current_checkpoint.get('chart_name_mapping', {})
            name_description_mapping = self.current_checkpoint.get('chart_name_description_mapping', {})

        run_result['chart_code_mapping'] = chart_code_mapping
        run_result['chart_name_mapping'] = name_mapping
        run_result['chart_name_description_mapping'] = name_description_mapping
        
        if self.current_phase == 'phase4':
            analysis_result = AnalysisResult(
                title=report_title,
                content=report_content,
                image_save_dir=self.image_save_dir,
                chart_code_mapping=chart_code_mapping,
                chart_name_mapping=name_mapping,
                chart_name_description_mapping=name_description_mapping
            )
            self.memory.add_data(analysis_result)
            self.memory.add_log(
                id=self.id,
                type=self.type,
                input_data=input_data,
                output_data=analysis_result,
                error=False,
                note=f"Analysis result generated successfully"
            )
            self.current_phase = 'done'
            await self.save(state={'current_phase': self.current_phase, 'analysis_result': analysis_result, 'finished': True}, checkpoint_name=checkpoint_name)
        self.memory.save()
        return run_result


class AnalysisResult:
    def __init__(
        self, 
        title: str, 
        content: str, 
        image_save_dir: str,
        chart_code_mapping: dict = None, 
        chart_name_mapping: dict = None, 
        chart_name_description_mapping: dict = None
    ):
        self.title = title
        self.content = content
        self.image_save_dir = image_save_dir
        self.chart_code_mapping = chart_code_mapping
        self.chart_name_mapping = chart_name_mapping
        self.chart_name_description_mapping = chart_name_description_mapping
    
    def __str__(self):
        # Replace placeholders with descriptive captions
        content = self._repalce_image_name()[1]
        return f"Report Title: {self.title}\nReport Content: {content}\n\n"

    def brief_str(self):
        # Replace placeholders with descriptive captions
        content = self._repalce_image_name()[1]
        return f"Report Title: {self.title}\nReport Content: {content[:300]}...(more content available)\n\n"
    
    def _repalce_image_name(self):
        image_name_list = []
        report_content = self.content
        img_list = re.findall("@import \"(.*?)\"", self.content)
        # Note: AnalysisResult is not an agent and has no logger; use prints or another mechanism if logging is needed.

        for img in img_list:
            if img in self.chart_name_description_mapping:
                new_img = self.chart_name_mapping[img]
                report_content = report_content.replace(
                    f"@import \"{img}\"",
                    f"@import \"{new_img}\"" + '\n(Description: ' + self.chart_name_description_mapping[img][:100] + ')'
                )
                image_name_list.append(new_img)
        return image_name_list, report_content
    
    def get_all_img(self):
        return self._repalce_image_name()[0]
