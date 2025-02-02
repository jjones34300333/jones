import ast
import copy
import functools
import inspect
import queue
import sys
import os
import json
import time
import traceback
import typing
import uuid
import warnings
from datetime import datetime
from random import randint

import filelock
import requests

if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from importlib.metadata import distribution, PackageNotFoundError

    assert distribution('hf_transfer') is not None
    have_hf_transfer = True
except (PackageNotFoundError, AssertionError):
    have_hf_transfer = False

if have_hf_transfer and os.getenv('HF_HUB_ENABLE_HF_TRANSFER', 'None') != '0':
    os.environ['HF_HUB_ENABLE_HF_TRANSFER'] = '1'

os.environ['SCARF_NO_ANALYTICS'] = 'true'
os.environ['DO_NOT_TRACK'] = 'true'
os.environ['OTEL_SDK_DISABLED'] = 'true'

os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.environ['BITSANDBYTES_NOWELCOME'] = '1'
warnings.filterwarnings('ignore', category=UserWarning, message='TypedStorage is deprecated')
os.environ['FIFTYONE_SHOW_PROGRESS_BARS'] = 'false'

# more is not useful typically, don't let these go beyond limits and eat up resources
max_cores = max(1, os.cpu_count() // 2)
if os.getenv('NUMEXPR_MAX_THREADS') is None:
    os.environ['NUMEXPR_MAX_THREADS'] = str(min(8, max_cores))
if os.getenv('NUMEXPR_NUM_THREADS') is None:
    os.environ['NUMEXPR_NUM_THREADS'] = str(min(8, max_cores))
if os.getenv('OMP_NUM_THREADS') is None:
    os.environ['OMP_NUM_THREADS'] = str(min(8, max_cores))
if os.getenv('OPENBLAS_NUM_THREADS') is None:
    os.environ['OPENBLAS_NUM_THREADS'] = str(min(8, max_cores))
if os.getenv('DUCKDB_NUM_THREADS') is None:
    os.environ['DUCKDB_NUM_THREADS'] = str(min(4, max_cores))
if os.getenv('RAYON_RS_NUM_CPUS') is None:
    os.environ['RAYON_RS_NUM_CPUS'] = str(min(8, max_cores))
if os.getenv('RAYON_NUM_THREADS') is None:
    os.environ['RAYON_NUM_THREADS'] = str(min(8, max_cores))

from gradio_funcs import merge_chat_conversation_history
from db_utils import fetch_user
from model_utils import switch_a_roo_llama, get_score_model, get_model_retry, get_model, \
    get_client_from_inference_server, model_lock_to_state

from evaluate_params import eval_func_param_names, no_default_param_names, input_args_list, image_size_default, \
    image_quality_choices
from enums import DocumentSubset, LangChainMode, no_lora_str, no_model_str, \
    LangChainAction, LangChainAgent, DocumentChoice, LangChainTypes, super_source_prefix, \
    super_source_postfix, t5_type, get_langchain_prompts, gr_to_lg, invalid_key_msg, docs_joiner_default, \
    docs_ordering_types_default, docs_token_handling_default, max_input_tokens_public, max_total_input_tokens_public, \
    max_top_k_docs_public, max_top_k_docs_default, max_total_input_tokens_public_api, max_top_k_docs_public_api, \
    max_input_tokens_public_api, anthropic_mapping, \
    base_langchain_actions, generic_prefix, \
    generic_postfix, langchain_modes_intrinsic, valid_imagechange_models, \
    valid_imagegen_models, valid_imagestyle_models, \
    langchain_modes0, langchain_mode_types0, langchain_mode_paths0, \
    llava_num_max, response_formats, noop_prompt_type, unknown_prompt_type, \
    json_object_prompt0, json_object_prompt_simpler0, json_code_prompt0, user_prompt_for_fake_system_prompt0, \
    json_schema_instruction0, json_code_prompt_if_no_schema0, my_db_state0, empty_prompt_type, is_gradio_vision_model, \
    is_json_model, is_vision_model, \
    model_state_none0, other_model_state_defaults0, image_batch_image_prompt0, image_batch_final_prompt0, \
    tokens_per_image, openai_supports_functiontools, openai_supports_parallel_functiontools, does_support_functiontools, \
    json_object_post_prompt_reminder0, json_code_post_prompt_reminder0, json_code2_post_prompt_reminder0, \
    max_stream_string_for_json

from utils import set_seed, clear_torch_cache, NullContext, wrapped_partial, EThread, get_githash, \
    import_matplotlib, get_device, makedirs, get_kwargs, start_faulthandler, get_hf_server, \
    have_langchain, set_openai, cuda_vis_check, H2O_Fire, lg_to_gr, str_to_list, str_to_dict, get_token_count, \
    have_wavio, have_soundfile, have_deepspeed, have_doctr, have_librosa, have_TTS, have_flash_attention_2, \
    have_diffusers, sanitize_filename, get_gradio_tmp, get_is_gradio_h2oai, get_json, \
    get_docs_tokens, deduplicate_names, have_autogen, get_model_name, is_empty, get_supports_schema

start_faulthandler()
import_matplotlib()

SEED = 1236
set_seed(SEED)

from typing import Union

import torch
from transformers import GenerationConfig, TextIteratorStreamer

from prompter import Prompter, non_hf_types, PromptType, get_prompt, generate_prompt, \
    openai_gpts, get_vllm_extra_dict, gradio_to_llm, history_for_llm, apply_chat_template, model_name_to_prompt_type
from stopping import get_stopping
from prompter_utils import get_use_chat_template, base64_decode_jinja_template

langchain_actions = [x.value for x in list(LangChainAction)]

langchain_agents_list = [x.value for x in list(LangChainAgent)]


def main(
        load_8bit: bool = False,
        load_4bit: bool = False,
        low_bit_mode: int = 1,
        load_half: bool = None,
        use_flash_attention_2=False,
        load_gptq: str = '',
        use_autogptq: bool = False,
        load_awq: str = '',
        load_exllama: bool = False,
        use_safetensors: bool = True,
        revision: str = None,
        use_gpu_id: bool = True,
        base_model: str = '',
        display_name: str = None,
        tokenizer_base_model: str = '',
        lora_weights: str = "",
        gpu_id: int = 0,
        compile_model: bool = None,
        use_cache: bool = None,
        inference_server: str = "",
        regenerate_clients: bool = True,
        regenerate_gradio_clients: bool = False,
        validate_clients: bool = True,
        fail_if_invalid_client: bool = False,

        prompt_type: Union[int, str] = None,
        prompt_dict: typing.Dict = None,
        chat_template: str = '',
        system_prompt: str = 'auto',
        allow_chat_system_prompt: bool = True,

        # llama and gpt4all settings
        llamacpp_path: str = 'llamacpp_path',
        llamacpp_dict: typing.Dict = dict(n_gpu_layers=100, use_mlock=True, n_batch=1024, n_gqa=0),
        model_path_llama: str = '',
        model_name_gptj: str = '',
        model_name_gpt4all_llama: str = '',
        model_name_exllama_if_no_config: str = '',
        exllama_dict: typing.Dict = dict(),
        gptq_dict: typing.Dict = dict(),
        attention_sinks: bool = False,
        sink_dict: typing.Dict = dict(),
        truncation_generation: bool = False,
        hf_model_dict: typing.Dict = dict(),
        force_seq2seq_type: bool = False,
        force_t5_type: bool = False,

        model_lock: typing.List[typing.Dict[str, str]] = None,
        model_lock_columns: int = None,
        model_lock_layout_based_upon_initial_visible: bool = False,
        fail_if_cannot_connect: bool = False,

        # input to generation
        temperature: float = None,
        top_p: float = None,
        top_k: int = None,
        penalty_alpha: float = None,
        num_beams: int = None,
        repetition_penalty: float = None,
        num_return_sequences: int = None,
        do_sample: bool = None,
        seed: int = None,
        max_new_tokens: int = None,
        min_new_tokens: int = None,
        early_stopping: Union[bool, str] = None,
        max_time: float = None,

        memory_restriction_level: int = None,
        debug: bool = False,
        save_dir: str = None,
        local_files_only: bool = False,
        resume_download: bool = True,
        use_auth_token: Union[str, bool] = False,
        admin_pass: str = None,
        trust_remote_code: Union[str, bool] = True,
        rope_scaling: dict = None,
        max_seq_len: int = None,
        max_output_seq_len: int = None,
        offload_folder: str = "offline_folder",

        src_lang: str = "English",
        tgt_lang: str = "Russian",

        prepare_offline_level: int = 0,
        cli: bool = False,
        cli_loop: bool = True,
        eval: bool = False,
        gradio: bool = True,
        function: bool = False,

        force_streaming_on_to_handle_timeouts: bool = True,

        openai_server: bool = True,
        openai_port: int = 5001 if sys.platform == "darwin" else 5000,
        openai_workers: int = 1,

        function_server: bool = False,
        function_server_port: int = 5003 if sys.platform == "darwin" else 5002,
        function_server_workers: int = 1,
        function_api_key: str = None,

        agent_server: bool = False,  # WIP
        agent_port: int = 5004 if sys.platform == "darwin" else 5004,
        agent_workers: int = 1,

        multiple_workers_gunicorn: bool = False,

        gradio_offline_level: int = 0,
        server_name: str = "0.0.0.0",
        share: bool = False,
        open_browser: bool = False,
        close_button: bool = True,
        shutdown_via_api: bool = False,
        root_path: str = "",
        ssl_verify: bool = True,
        ssl_keyfile: str | None = None,
        ssl_certfile: str | None = None,
        ssl_keyfile_password: str | None = None,

        chat: bool = True,
        chat_conversation: typing.List[typing.Tuple[str, str]] = None,
        text_context_list: typing.List[str] = None,

        stream_output: bool = True,
        enable_caching: bool = False,
        async_output: bool = True,
        num_async: int = 3,
        stream_map: bool = False,

        show_examples: bool = None,
        verbose: bool = False,
        h2ocolors: bool = True,
        dark: bool = False,  # light tends to be best
        height: int = 600,
        render_markdown: bool = True,
        show_lora: bool = True,
        show_llama: bool = True,
        show_gpt4all: bool = False,
        login_mode_if_model0: bool = False,
        block_gradio_exit: bool = True,
        concurrency_count: int = None,
        api_open: bool = False,
        allow_api: bool = True,
        system_api_open: bool = False,
        input_lines: int = 1,
        gradio_size: str = None,
        show_copy_button: bool = True,
        large_file_count_mode: bool = False,
        gradio_ui_stream_chunk_size: int = None,
        gradio_ui_stream_chunk_min_seconds: float = 0.2,
        gradio_ui_stream_chunk_seconds: float = 2.0,
        gradio_api_use_same_stream_limits: bool = True,
        gradio_upload_to_chatbot: bool = False,
        gradio_upload_to_chatbot_num_max: bool = 2,
        gradio_errors_to_chatbot: bool = True,

        pre_load_embedding_model: bool = True,
        embedding_gpu_id: Union[int, str] = 'auto',

        auth: Union[typing.List[typing.Tuple[str, str]], str] = None,
        auth_filename: str = None,
        auth_access: str = 'open',
        auth_freeze: bool = False,
        auth_message: str = None,
        google_auth: bool = False,
        guest_name: str = None,
        enforce_h2ogpt_api_key: bool = None,
        enforce_h2ogpt_ui_key: bool = None,
        h2ogpt_api_keys: Union[list, str] = [],
        h2ogpt_key: str = None,
        extra_allowed_paths: list = [],
        blocked_paths: list = [],

        max_max_time=None,
        max_max_new_tokens=None,

        visible_models: list = None,
        max_visible_models: int = None,

        visible_ask_anything_high: bool = True,
        visible_visible_models: bool = True,
        visible_submit_buttons: bool = True,
        visible_side_bar: bool = True,
        visible_document_subset: bool = True,
        visible_max_quality: bool = True,
        visible_add_doc_to_chat: bool = True,
        visible_chat_history: bool = True,
        visible_doc_track: bool = True,

        visible_chat_tab: bool = True,
        visible_doc_selection_tab: bool = True,
        visible_doc_view_tab: bool = True,
        visible_chat_history_tab: bool = True,
        visible_expert_tab: bool = True,
        visible_models_tab: bool = True,
        visible_system_tab: bool = True,
        visible_tos_tab: bool = False,
        visible_login_tab: bool = True,
        visible_hosts_tab: bool = False,
        visible_langchain_action_radio: bool = True,
        visible_langchain_purge: bool = True,

        chat_tabless: bool = False,
        visible_h2ogpt_links: bool = True,
        visible_h2ogpt_qrcode: bool = True,
        visible_h2ogpt_logo: bool = True,
        visible_chatbot_label: bool = True,
        visible_all_prompter_models: bool = False,
        visible_curated_models: bool = True,
        actions_in_sidebar: bool = False,
        document_choice_in_sidebar: bool = True,
        enable_add_models_to_list_ui: bool = False,
        max_raw_chunks: int = None,
        pdf_height: int = 800,
        avatars: bool = True,
        add_disk_models_to_ui: bool = True,
        page_title: str = "h2oGPT",
        model_label_prefix: str = "h2oGPT",
        favicon_path: str = None,
        visible_ratings: bool = False,
        reviews_file: str = None,

        sanitize_user_prompt: bool = False,
        sanitize_bot_response: bool = False,

        extra_model_options: typing.List[str] = [],
        extra_lora_options: typing.List[str] = [],
        extra_server_options: typing.List[str] = [],

        score_model: str = 'auto',
        verifier_model: str = None,
        verifier_tokenizer_base_model: str = None,
        verifier_inference_server: str = None,

        eval_filename: str = None,
        eval_prompts_only_num: int = 0,
        eval_prompts_only_seed: int = 1234,
        eval_as_output: bool = False,

        langchain_mode: str = None,
        user_path: str = None,
        langchain_modes: list = langchain_modes0,
        langchain_mode_paths: dict = langchain_mode_paths0,
        langchain_mode_types: dict = langchain_mode_types0,
        detect_user_path_changes_every_query: bool = False,
        update_selection_state_from_cli: bool = True,

        langchain_action: str = LangChainAction.QUERY.value,
        langchain_agents: list = [],
        force_langchain_evaluate: bool = False,

        visible_langchain_actions: list = base_langchain_actions.copy(),
        visible_langchain_agents: list = langchain_agents_list.copy(),

        document_subset: str = DocumentSubset.Relevant.name,
        document_choice: list = [DocumentChoice.ALL.value],
        document_source_substrings: list = [],
        document_source_substrings_op: str = 'and',
        document_content_substrings: list = [],
        document_content_substrings_op: str = 'and',

        use_llm_if_no_docs: bool = True,
        load_db_if_exists: bool = True,
        keep_sources_in_context: bool = False,
        db_type: str = 'chroma',
        use_openai_embedding: bool = False,
        use_openai_model: bool = False,
        hf_embedding_model: str = None,
        migrate_embedding_model: str = False,
        cut_distance: float = 1.64,
        answer_with_sources: bool = True,
        append_sources_to_answer: bool = False,
        append_sources_to_chat: bool = True,
        sources_show_text_in_accordion: bool = True,
        top_k_docs_max_show: int = 10,
        show_link_in_sources: bool = True,
        langchain_instruct_mode: bool = True,

        pre_prompt_query: str = None,
        prompt_query: str = None,
        pre_prompt_summary: str = None,
        prompt_summary: str = None,
        hyde_llm_prompt: str = None,
        all_docs_start_prompt: str = 'auto',
        all_docs_finish_prompt: str = 'auto',

        user_prompt_for_fake_system_prompt: str = None,
        json_object_prompt: str = None,
        json_object_prompt_simpler: str = None,
        json_code_prompt: str = None,
        json_code_prompt_if_no_schema: str = None,
        json_schema_instruction: str = None,
        json_preserve_system_prompt: bool = False,
        json_object_post_prompt_reminder: str = None,
        json_code_post_prompt_reminder: str = None,
        json_code2_post_prompt_reminder: str = None,

        add_chat_history_to_context: bool = True,
        add_search_to_context: bool = False,
        context: str = '',
        iinput: str = '',
        allow_upload_to_user_data: bool = True,
        reload_langchain_state: bool = True,
        allow_upload_to_my_data: bool = True,
        enable_url_upload: bool = True,
        enable_text_upload: bool = True,
        enable_sources_list: bool = True,
        chunk: bool = True,
        chunk_size: int = 512,
        top_k_docs: int = None,
        docs_ordering_type: str = docs_ordering_types_default,
        min_max_new_tokens=512,
        max_input_tokens=None,
        max_total_input_tokens=None,
        docs_token_handling: str = docs_token_handling_default,
        docs_joiner: str = docs_joiner_default,
        hyde_level: int = 0,
        hyde_template: str = None,
        hyde_show_only_final: bool = False,
        hyde_show_intermediate_in_accordion: bool = True,
        map_reduce_show_intermediate_in_accordion: bool = True,
        doc_json_mode: bool = False,
        metadata_in_context: Union[str, list] = 'auto',

        auto_reduce_chunks: bool = True,
        max_chunks: int = 100,
        headsize: int = 50,
        n_jobs: int = -1,
        n_gpus: int = None,
        clear_torch_cache_level: int = 1,

        # urls
        use_unstructured: bool = True,
        use_playwright: bool = False,
        use_selenium: bool = False,
        use_scrapeplaywright: bool = False,
        use_scrapehttp: bool = False,

        # pdfs
        use_pymupdf: Union[bool, str] = 'auto',
        use_unstructured_pdf: Union[bool, str] = 'auto',
        use_pypdf: Union[bool, str] = 'auto',
        enable_pdf_ocr: Union[bool, str] = 'auto',
        enable_pdf_doctr: Union[bool, str] = 'auto',
        try_pdf_as_html: Union[bool, str] = 'auto',

        # images
        enable_ocr: bool = False,
        enable_doctr: bool = True,
        enable_pix2struct: bool = False,
        enable_captions: bool = True,
        enable_llava: bool = True,
        enable_transcriptions: bool = True,

        pre_load_image_audio_models: bool = False,

        caption_gpu: bool = True,
        caption_gpu_id: Union[int, str] = 'auto',
        captions_model: str = "microsoft/Florence-2-base",
        doctr_gpu: bool = True,
        doctr_gpu_id: Union[int, str] = 'auto',
        llava_model: str = None,
        llava_prompt: str = 'auto',

        image_file: str = None,
        image_control: str = None,
        images_num_max: int = None,
        image_resolution: tuple = None,
        image_format: str = None,
        rotate_align_resize_image: bool = None,
        video_frame_period: int = None,
        image_batch_image_prompt: str = None,
        image_batch_final_prompt: str = None,
        image_batch_stream: bool = False,
        visible_vision_models: Union[str, int, list] = None,
        video_file: str = None,

        response_format: str = 'text',
        guided_json: Union[str, dict] = '',
        guided_regex: str = '',
        guided_choice: typing.List[str] = None,
        guided_grammar: str = '',
        guided_whitespace_pattern: str = None,

        asr_model: str = "openai/whisper-medium",
        asr_gpu: bool = True,
        asr_gpu_id: Union[int, str] = 'auto',
        asr_use_better: bool = True,
        asr_use_faster: bool = False,

        enable_stt: Union[str, bool] = 'auto',
        stt_model: str = "openai/whisper-base.en",
        stt_gpu: bool = True,
        stt_gpu_id: Union[int, str] = 'auto',
        stt_continue_mode: int = 1,

        enable_tts: Union[str, bool] = 'auto',
        tts_gpu: bool = True,
        tts_gpu_id: Union[int, str] = 'auto',
        tts_model: str = 'microsoft/speecht5_tts',

        tts_gan_model: str = 'microsoft/speecht5_hifigan',
        tts_coquiai_deepspeed: bool = False,
        tts_coquiai_roles: dict = None,

        chatbot_role: str = "None",  # "Female AI Assistant",
        speaker: str = "None",  # "SLT (female)",
        tts_language: str = 'autodetect',
        tts_speed: float = 1.0,
        tts_action_phrases: typing.List[str] = [],  # ['Nimbus'],
        tts_stop_phrases: typing.List[str] = [],  # ['Yonder'],
        sst_floor: float = 100,

        enable_image: bool = False,
        visible_image_models: typing.List[str] = [],
        image_size: str = image_size_default,
        image_quality: str = 'standard',
        image_guidance_scale: float = 3.0,
        image_num_inference_steps: int = 30,
        image_gpu_ids: typing.List[Union[str, int]] = None,
        enable_llava_chat: bool = False,

        # json
        jq_schema='.[]',

        extract_frames: int = 10,

        max_quality: bool = False,

        enable_heap_analytics: bool = True,
        heap_app_id: str = "1680123994",
        client_metadata: str = '',

        cert_lookup_directory: str = "/etc/ssl/more-certs",
):
    """

    :param load_8bit: load model in 8-bit using bitsandbytes
    :param load_4bit: load model in 4-bit using bitsandbytes
    :param low_bit_mode: 0: no quantization config 1: change compute 2: nf4 3: double quant 4: 2 and 3
           See: https://huggingface.co/docs/transformers/main_classes/quantization
           If using older bitsandbytes or transformers, 0 is required
    :param load_half: load model in float16 (None means auto, which means True unless t5 based model)
                      otherwise specify bool
    :param use_flash_attention_2: Whether to try to use flash attention 2 if available when loading HF models
           Warning: We have seen nans and type mismatches with flash-attn==2.3.4 installed and this enabled,
                    even for other models like embedding model that is unrelated to primary models.
    :param load_gptq: to load model with GPTQ, put model_basename here, e.g. 'model' for TheBloke models
    :param use_autogptq: whether to use AutoGPTQ (True) or HF Transformers (False)
           Some models are only supported by one or the other
    :param load_awq: load model with AWQ, e.g. 'model' for TheBloke models
    :param load_exllama: whether to use exllama (only applicable to LLaMa1/2 models with 16-bit or GPTQ
    :param use_safetensors: to use safetensors version (assumes file/HF points to safe tensors version)
    :param revision: Which HF revision to use
    :param use_gpu_id: whether to control devices with gpu_id.  If False, then spread across GPUs
    :param base_model: model HF-type name.  If use --base_model to preload model, cannot unload in gradio in models tab
    :param display_name: display name for model (used in UI and API to access)
    :param tokenizer_base_model: tokenizer HF-type name.  Usually not required, inferred from base_model.
           If model is private or doesn't exist as HF model, can use "tiktoken" and pass max_seq_len and (if different) max_output_seq_len
           For inference servers like OpenAI etc. if have model name, we use tiktoken with known input/output sequence lengths.
    :param lora_weights: LORA weights path/HF link
    :param gpu_id: if use_gpu_id, then use gpu_id for cuda device ID, or auto mode if gpu_id != -1
    :param compile_model Whether to compile the model
    :param use_cache: Whether to use caching in model (some models fail when multiple threads use)
    :param inference_server: Consume base_model as type of model at this address
                             Address can be text-generation-server hosting that base_model
                             e.g. python generate.py --inference_server="http://192.168.1.46:6112" --base_model=HuggingFaceH4/zephyr-7b-beta

                             For a gradio server, use same as TGI server.  We infer if it's TGI or Gradio.
                             e.g. python generate.py --inference_server="http://192.168.1.46:7860" --base_model=HuggingFaceH4/zephyr-7b-beta
                             For auth protected gradio, do:
                             e.g. python generate.py --inference_server="http://192.168.1.46:7860:user:password" --base_model=HuggingFaceH4/zephyr-7b-beta
                             If don't want to specify port, do:
                             e.g. python generate.py --inference_server="https://gpt.h2o.ai:None:user:password" --base_model=HuggingFaceH4/zephyr-7b-beta


                             Or Address can be "openai_chat" or "openai" for OpenAI API
                             Or Address can be "openai_azure_chat" or "openai_azure" for Azure OpenAI API
                             e.g. python generate.py --inference_server="openai_chat" --base_model=gpt-3.5-turbo
                             e.g. python generate.py --inference_server="openai" --base_model=text-davinci-003
                             e.g. python generate.py --inference_server="openai_azure_chat:<deployment_name>:<baseurl>:<api_version>:<access key>" --base_model=gpt-3.5-turbo
                             e.g. python generate.py --inference_server="openai_azure:<deployment_name>:<baseurl>:<api_version>:<access key>" --base_model=text-davinci-003
                             Optionals (Replace with None or just leave empty but keep :)
                                 <deployment_name> of some deployment name
                                 <baseurl>: e.g. "<endpoint>.openai.azure.com" for some <endpoint> without https://
                                 <api_version> of some api, e.g. 2023-05-15

                             Or Address can be for vLLM:
                              Use: "vllm:IP:port" for OpenAI-compliant vLLM endpoint
                              Use: "vllm_chat:IP:port" for OpenAI-Chat-compliant vLLM endpoint

                              Use: "vllm:http://IP:port/v1" for OpenAI-compliant vLLM endpoint
                              Use: "vllm_chat:http://IP:port/v1" for OpenAI-Chat-compliant vLLM endpoint

                              Use: "vllm:https://IP/v1" for OpenAI-compliant vLLM endpoint
                              Use: "vllm_chat:https://IP/v1" for OpenAI-Chat-compliant vLLM endpoint

                              For example, for standard URL and API key for vllm, one would do:
                                 vllm_chat:https://vllm.h2o.ai:None:/v1:1234ABCD
                                 or for non-standard URL:
                                 vllm_chat:https://vllm.h2o.ai:None:/1b1219f7-4bb4-43e9-881f-fa8fa9fe6e04/v1:1234ABCD
                                 where vllm.h2o.ai is the DNS name of the IP, None means no extra port, so will be dropped from base_url when using API, /1b1219f7-4bb4-43e9-881f-fa8fa9fe6e04/v1 is the url of the "page" to access, and 1234ABCD is the api key
                              Or for example:
                                 vllm_chat:https://vllm.h2o.ai:5001:/1b1219f7-4bb4-43e9-881f-fa8fa9fe6e04/v1:1234ABCD
                                 where vllm.h2o.ai is the DNS name of the IP, 5001 is the port, /1b1219f7-4bb4-43e9-881f-fa8fa9fe6e04/v1 is the url of the "page" to access, and 1234ABCD is the api key

                            If you have any other OpenAI compatible chat completion endpoint, you should use vllm_chat way.  E.g. llama.cpp http server: https://github.com/ggerganov/llama.cpp/tree/master/examples/server

                            For sglang, text models are supported via OpenAI API and can use vllm_chat or vllm as usual.
                            For sglang and vision models, need to specify sglang so we use http requests API via generate endpoint.  Use "sglang" prefix and otherwise it is like vllm endpoint
                            Currently it's not clear how to make an API key work: https://github.com/sgl-project/sglang/issues/466, so one should rely upon firewalls
                                One should also pass the name of the python module used for conversation, e.g. for
                                  python -m sglang.launch_server --model-path lmms-lab/llama3-llava-next-8b --tokenizer-path lmms-lab/llama3-llava-next-8b-tokenizer --port=30000 --host="0.0.0.0" --tp-size=1 --random-seed=1234 --context-length=8192
                                One should use:
                                  sglang:conv_llava_llama_3:http://IP:port

                            For together.ai that is OpenAI compliant, use:
                                vllm_chat:https://api.together.xyz:None:/v1:1234ABCD

                              Or for groq, can use OpenAI API like:
                               GROQ IS BROKEN FOR OPENAI API:
                                   vllm:https://api.groq.com/openai:None:/v1:<api key>'
                                   with: other model_lock or CLI options: {'inference_server': 'vllm:https://api.groq.com/openai:None:/v1:<api key>', 'base_model':'mixtral-8x7b-32768', 'visible_models':'mixtral-8x7b-32768', 'max_seq_len': 31744, 'prompt_type':'plain'}
                                   i.e.ensure to use 'plain' prompt, not mixtral.
                              For groq:
                                 groq and ensures set env GROQ_API_KEY
                                 or groq:<api key>
                                 with: other model_lock or CLI options: {'inference_server': 'groq:<api key>', 'base_model':'mixtral-8x7b-32768', 'visible_models':'mixtral-8x7b-32768', 'max_seq_len': 31744, 'prompt_type':'plain'}

                             Or Address can be replicate:
                             Use:
                              --inference_server=replicate:<model name string> will use a Replicate server, requiring a Replicate key.
                              e.g. <model name string> looks like "a16z-infra/llama13b-v2-chat:df7690f1994d94e96ad9d568eac121aecf50684a0b0963b25a41cc40061269e5"

                             Or Address can be for AWS SageMaker:
                              Use: "sagemaker_chat:<endpoint name>" for chat models that AWS sets up as dialog
                              Use: "sagemaker:<endpoint name>" for foundation models that AWS only text as inputs

                             Or Address can be for Anthropic Claude.  Ensure key is set in env ANTHROPIC_API_KEY
                              Use: "anthropic
                              E.g. --base_model=claude-2.1 --inference_server=anthropic

                             Or Address can be for Google Gemini.  Ensure key is set in env GOOGLE_API_KEY
                              Use: "google"
                              E.g. --base_model=gemini-pro --inference_server=google

                             Or Address can be for MistralAI.  Ensure key is set in env MISTRAL_API_KEY
                              Use: "mistralai"
                              E.g. --base_model=mistral-medium --inference_server=mistralai

    :param regenerate_clients: Whether to regenerate client every LLM call or use start-up version
           Benefit of doing each LLM call is timeout can be controlled to max_time in expert settings, else we use default of 600s.
           Maybe risky, some lack of thread safety: https://github.com/encode/httpx/discussions/3043, so disabled
           Because gradio clients take long time to start-up, we don't ever regenerate them each time (including llava models)
    :param regenerate_gradio_clients: Whether to also regenerate gradio clients (slow)
    :param validate_clients: Whether to validate clients, and if invalid, do not add them to list (e.g. if OpenAI API key is invalid, then just report in logs, do not hard fail, but do not add the model to model list)
           Currently only done for OpenAI or vLLM endpoints
    :param fail_if_invalid_client: Whether to fail hard if any client fails validation

    :param prompt_type: type of prompt, usually matched to fine-tuned model or plain for foundational model
    :param prompt_dict: If prompt_type=custom, then expects (some) items returned by get_prompt(..., return_dict=True)
    :param chat_template: jinja HF transformers chat_template to use.  '' or None means no change to template
           Sometimes hard to pass string with proper escapes etc.  So string can be base64 encoded with base64_encode_jinja_template()
    :param system_prompt: Universal system prompt to use if model supports, like LLaMa2, regardless of prompt_type definition.
           Useful for langchain case to control behavior, or OpenAI and Replicate.
           If None, 'None', or 'auto', then for LLaMa or other models that internally have system_prompt, will use default for each model
           If '', then no system prompt (no empty template given to model either, just no system part added at all)
           If some string not in ['None', 'auto'], then use that as system prompt
           Default is '', no system_prompt, because often it hurts performance/accuracy
    :param allow_chat_system_prompt:
           Whether to use conversation_history to pre-append system prompt

    :param llamacpp_path: Location to store downloaded gguf or load list of models from
           Note HF models go into hf cache folder, and gpt4all models go into their own cache folder
           Can override with ENV LLAMACPP_PATH
    :param llamacpp_dict:
           n_gpu_layers: for llama.cpp based models, number of GPU layers to offload (default is all by using large value)
           use_mlock: when using `llama.cpp` based CPU models, for computers with low system RAM or slow CPUs, recommended False
           n_batch: Can make smaller to 128 for slower low-memory CPU systems
           n_gqa: Required to be 8 for LLaMa 70B
           ... etc. anything that could be passed to llama.cpp or GPT4All models
           e.g. python generate.py --base_model='llama' --prompt_type=llama2 --score_model=None --langchain_mode='UserData' --user_path=user_path --llamacpp_dict="{'n_gpu_layers':25,'n_batch':128}"
    :param model_path_llama: model path or URL (for auto-download)
    :param model_name_gptj: model path or URL (for auto-download)
    :param model_name_gpt4all_llama: model path or URL (for auto-download)
    :param model_name_exllama_if_no_config: exllama model's full path for model, tokenizer, generator for use when no HuggingFace config
    :param exllama_dict for setting various things for Exllama class
           E.g. compress_pos_emb,
                set_auto_map,
                gpu_peer_fix,
                alpha_value,
                matmul_recons_thd,
                fused_mlp_thd
                sdp_thd
                fused_attn
                matmul_fused_remap
                rmsnorm_no_half2
                rope_no_half2
                matmul_no_half2
                silu_no_half2
                concurrent_streams
           E.g. to set memory to be split across 2 GPUs, use --exllama_dict="{'set_auto_map':20,20}"
    :param gptq_dict: Choices for AutoGPTQ, e.g. one can change defaults to these non-defaults:
         inject_fused_attention=False
         disable_exllama=True
         use_triton=True
    :param attention_sinks: Whether to enable attention sinks.
    :param sink_dict: dict of options for attention sinks
           E.g. {'window_length': 1024, 'num_sink_tokens': 4}
           Default is window length same size as max_input_tokens (max_seq_len if max_input_tokens not set)
    :param hf_model_dict: dict of options for HF models using transformers

    :param truncation_generation: Whether (for torch) to terminate generation once reach context length of model.
            For some models, perplexity becomes critically large beyond context
            For other models like Mistral, one can generate beyond max_seq_len set to 4096 or 8192 without issue, since based upon 32k embeddings
            codellama can also generate beyond its 16k context length
            So default is off, but for simpler/older models True may be wise to avoid bad generations

    :param model_lock: Lock models to specific combinations, for ease of use and extending to many models
           Only used if gradio = True
           List of dicts, each dict has base_model, tokenizer_base_model, lora_weights, inference_server, prompt_type, and prompt_dict
           If all models have same prompt_type, and prompt_dict, can still specify that once in CLI outside model_lock as default for dict
           Can specify model_lock instead of those items on CLI
           As with CLI itself, base_model can infer prompt_type and prompt_dict if in prompter.py.
             Also, tokenizer_base_model and lora_weights are optional.
             Also, inference_server is optional if loading model from local system.
           All models provided will automatically appear in compare model mode
           Model loading-unloading and related choices will be disabled.  Model/lora/server adding will be disabled
    :param model_lock_columns: How many columns to show if locking models (and so showing all at once)
           If None, then defaults to up to 3
           if -1, then all goes into 1 row
           Maximum value is 4 due to non-dynamic gradio rendering elements
    :param model_lock_layout_based_upon_initial_visible: Whether to base any layout upon visible models (True)
           or upon all possible models.  gradio does not allow dynamic objects, so all layouts are preset,
           and these are two reasonable options.
           False is best when there are many models and user excludes middle ones as being visible.
    :param fail_if_cannot_connect: if doing model locking (e.g. with many models), fail if True.  Otherwise ignore.
           Useful when many endpoints and want to just see what works, but still have to wait for timeout.

    :param temperature: generation temperature
    :param top_p: generation top_p
    :param top_k: generation top_k
    :param penalty_alpha: penalty_alpha>0 and top_k>1 enables contrastive search (not all models support)
    :param num_beams: generation number of beams
    :param repetition_penalty: generation repetition penalty
    :param num_return_sequences: generation number of sequences (1 forced for chat)
    :param do_sample: generation sample.  Enable for sampling for given temperature, top_p, top_k, else greedy decoding and then temperature, top_p, top_k not used.
        https://huggingface.co/docs/transformers/main_classes/text_generation#transformers.GenerationConfig.do_sample
        https://txt.cohere.com/llm-parameters-best-outputs-language-ai/
        https://medium.com/@daniel.puenteviejo/the-science-of-control-how-temperature-top-p-and-top-k-shape-large-language-models-853cb0480dae
    :param seed: seed (0 means random seed, >0 uses that seed for sampling so reproducible even for sampling).  None becomes 0.
    :param max_new_tokens: generation max new tokens
    :param min_new_tokens: generation min tokens
    :param early_stopping: generation early stopping
    :param max_time: maximum time to allow for generation
    :param memory_restriction_level: 0 = no restriction to tokens or model, 1 = some restrictions on token 2 = HF like restriction 3 = very low memory case
    :param debug: enable debug mode
    :param save_dir: directory chat data is saved to
    :param local_files_only: whether to only use local files instead of doing to HF for models
    :param resume_download: whether to resume downloads from HF for models
    :param use_auth_token: whether to use HF auth token (requires CLI did huggingface-cli login before)
    :param admin_pass: Administrator password
    :param trust_remote_code: whether to use trust any code needed for HF model
    :param rope_scaling:
           For HF transformers model: scaling for rope-based models.
           For long context models that have been tuned for a specific size, you have to only use that specific size by setting the `--rope_scaling` exactly correctly
            e.g. --rope_scaling="{'type':'dynamic', 'factor':4}"
            e.g. --rope_scaling="{'type':'linear', 'factor':4}"
            e.g. python generate.py --rope_scaling="{'type':'linear','factor':4}" --base_model=lmsys/vicuna-13b-v1.5-16k --hf_embedding_model=sentence-transformers/all-MiniLM-L6-v2 --load_8bit=True --langchain_mode=UserData --user_path=user_path --prompt_type=vicuna11 --h2ocolors=False
           For exllama model: --rope_scaling="{'alpha_value':4}" .  This automatically scales max_seq_len for exllama
    :param max_seq_len: Manually set maximum sequence length for the LLM
    :param max_output_seq_len: Manually set maximum output length for the LLM
    :param offload_folder: path for spilling model onto disk
    :param src_lang: source languages to include if doing translation (None = all)
    :param tgt_lang: target languages to include if doing translation (None = all)

    :param prepare_offline_level:
           Whether to just prepare for offline use, do not go into cli, eval, or gradio run modes
           0 : no prep
           1: prepare just h2oGPT with exact same setup as passed to CLI and ensure all artifacts for h2oGPT alone added to ~/.cache/
           2: prepare h2oGPT + all inference servers so h2oGPT+inference servers can use the ~/.cache/
    :param cli: whether to use CLI (non-gradio) interface.
    :param eval: whether to run evals
    :param cli_loop: whether to loop for CLI (False usually only for testing)
    :param gradio: whether to enable gradio, or to enable benchmark mode
    :param function: whether to run function mode to just return locals for function server

    :param force_streaming_on_to_handle_timeouts: whether to force streaming internally even if UI/API doesn't do it, so can handle timeouts and avoid blocking calls.

    :param openai_server: whether to launch OpenAI proxy server for local gradio server
           Disabled if API is disabled
    :param openai_port: port for OpenAI proxy server
    :param openai_workers: number of workers for OpenAI (1 means 1 worker, 0 means all physical cores, else choose)

    :param function_server: whether to launch Function server to handle document loading offloading to separate thread or forks
    :param function_server_port: port for OpenAI proxy server
    :param function_server_workers: number of workers for Function Server (1 means 1 worker, 0 means all physical cores, else choose)
    :param function_api_key: API key for function server, auto-set if not provided, uses first key like OpenAI proxy server does as well

    :param agent_server: whether to launch Agent proxy server
           Disabled if API is disabled
    :param agent_port: port for Agent proxy server
    :param agent_workers: number of workers for Agent Server (1 means 1 worker, 0 means all physical cores, else choose)

    :param multiple_workers_gunicorn: whether to use gunicorn (True) or uvicorn (False) for multiple workers

    :param gradio_offline_level: > 0, then change fonts so full offline
           == 1 means backend won't need internet for fonts, but front-end UI might if font not cached
           == 2 means backend and frontend don't need internet to download any fonts.
           Note: Some things always disabled include HF telemetry, gradio telemetry, chromadb posthog that involve uploading.
           This option further disables google fonts for downloading, which is less intrusive than uploading,
           but still required in air-gapped case.  The fonts don't look as nice as google fonts, but ensure full offline behavior.
           Also set --share=False to avoid sharing a gradio live link.
    :param server_name: IP to use.  In linux 0.0.0.0 is good choice so exposed to outside host, else for only local use 127.0.0.1.
                        For windows/MAC 0.0.0.0 or 127.0.0.1 will work, but may need to specify actual LAN IP address for other LAN clients to see.
    :param share: whether to share the gradio app with sharable URL
    :param open_browser: whether to automatically open browser tab with gradio UI
    :param close_button: Whether to show close button in system tab (if not public)
    :param shutdown_via_api: Whether to allow shutdown via API
    :param root_path: The root path (or "mount point") of the application,
           if it's not served from the root ("/") of the domain. Often used when the application is behind a reverse proxy
           that forwards requests to the application. For example, if the application is served at "https://example.com/myapp",
           the `root_path` should be set to "/myapp".
    :param ssl_verify: passed go gradio launch
    :param ssl_keyfile: passed go gradio launch
    :param ssl_certfile: passed go gradio launch
    :param ssl_keyfile_password: passed go gradio launch

    :param chat: whether to enable chat mode with chat history
    :param chat_conversation: list of tuples of (human, bot) conversation pre-appended to existing chat when using instruct/chat models
           Requires also add_chat_history_to_context = True
           It does *not* require chat=True, so works with nochat_api etc.
    :param text_context_list: List of strings to add to context for non-database version of document Q/A for faster handling via API etc.
           Forces LangChain code path and uses as many entries in list as possible given max_seq_len, with first assumed to be most relevant and to go near prompt.

    :param stream_output: whether to stream output
    :param enable_caching: whether to enable caching (Only for anthropic)
    :param async_output: Whether to do asyncio handling
           For summarization
           Applicable to HF TGI server
           Only if stream_output=False in CLI, UI, or API
    :param num_async: Number of simultaneously allowed asyncio calls to make for async_output
           Too many will overload inference server, too few will be too slow
    :param stream_map: Whether to stream map_reduce fully even while doing async (if async, then only first map in any group map will be streamed)
                       Experimental, not working fully.

    :param show_examples: whether to show clickable examples in gradio
    :param verbose: whether to show verbose prints
    :param h2ocolors: whether to use H2O.ai theme
    :param dark: whether to use dark mode for UI by default (still controlled in UI)
    :param height: height of chat window
    :param render_markdown: Whether to render markdown in chatbot UI.  In some cases this distorts the rendering.
           https://github.com/gradio-app/gradio/issues/4344#issuecomment-1771963021
    :param show_lora: whether to show LORA options in UI (expert so can be hard to understand)
    :param show_llama: whether to show LLaMa.cpp/GPT4All options in UI (only likely useful if have weak GPUs)
    :param show_gpt4all: whether to show GPT4All models in UI (not often useful, llama.cpp models best)
    :param login_mode_if_model0: set to True to load --base_model after client logs in, to be able to free GPU memory when model is swapped
    :param block_gradio_exit: whether to block gradio exit (used for testing)
    :param concurrency_count: gradio concurrency count (1 is optimal for local LLMs to avoid sharing cache that messes up models, else 64 is used if hosting remote inference servers only)
    :param api_open: If False, don't let API calls skip gradio queue
    :param allow_api: whether to allow API calls at all to gradio server
    :param input_lines: how many input lines to show for chat box (>1 forces shift-enter for submit, else enter is submit)
    :param gradio_size: Overall size of text and spaces: "xsmall", "small", "medium", "large".
           Small useful for many chatbots in model_lock mode
    :param show_copy_button: Whether to show copy button for chatbots
    :param large_file_count_mode: Whether to force manual update to UI of drop-downs, good idea if millions of chunks or documents
    :param gradio_ui_stream_chunk_size: Number of characters to wait before pushing text to ui.
           None is default, which is 0 when not doing model lock.  Else 20 by default.
           20 is reasonable value for fast models and fast systems when handling several models at once
           Choose 0 to disable (this disables use of gradio_ui_stream_chunk_min_seconds and gradio_ui_stream_chunk_seconds too)
           Work around for these bugs that lead to UI being overwhelmed under various cases
           https://github.com/gradio-app/gradio/issues/5914
           https://github.com/gradio-app/gradio/issues/6609
    :param gradio_ui_stream_chunk_min_seconds: Number of seconds before allow yield to avoid spamming yields at rate user would not care about, regardless of chunk_size
    :param gradio_ui_stream_chunk_seconds: Number of seconds to yield regardless of reaching gradio_ui_stream_chunk_size as long as something to yield
           Helps case when streaming is slow and want to see progress at least every couple seconds
    :param gradio_api_use_same_stream_limits: Whether to use same streaming limits as UI for API
    :param gradio_upload_to_chatbot: Whether to show upload in chatbots
    :param gradio_upload_to_chatbot_num_max: Max number of things to add to chatbot
    :param gradio_errors_to_chatbot: Whether to show errors in Accordion in chatbot or just in exceptions in each tab

    :param pre_load_embedding_model: Whether to preload embedding model for shared use across DBs and users (multi-thread safe only)
    :param embedding_gpu_id: which GPU to place embedding model on.
                             Only used if preloading embedding model.
                             If 'auto', then use first device as is default
                             If 'cpu' or some other string like 'mps', then use that as device name.

    :param auth: gradio auth for launcher in form [(user1, pass1), (user2, pass2), ...]
                 e.g. --auth=[('jon','password')] with no spaces
                 e.g. --auth="[('jon', 'password)())(')]" so any special characters can be used
                 e.g. --auth=auth.db to specify persisted state file with name auth.db (auth_filename then not required)
                 e.g. --auth='' will use default auth.db as file name for persisted state file (auth_filename good idea to control location)
                 e.g. --auth=None will use no auth, but still keep track of auth state, just not from logins
    :param auth_filename:
         Set auth filename, used only if --auth= was passed list of user/passwords

    If use auth.db will use sqlite3 database for auth for faster access for large number of users
    If you had .json and want to use faster .db, just pass filename with .db instead of .json and at startup it will be migrated automatically to .db and used.

    :param auth_access:
         'open': Allow new users to be added
         'closed': Stick to existing users
    :param auth_freeze: whether freeze authentication based upon current file, no longer update file
    :param auth_message: Message to show if having users login, fixed if passed, else dynamic internally
    :param google_auth: Whether to use google auth
    :param guest_name: guess name if using auth and have open access.
           If '', then no guest allowed even if open access, then all databases for each user always persisted
           If None, then set to 'guest' for open access, or '' for closed access
           For open or closed access, if guest_name is set, that forms prefix of actual internal userID apart from authentication and can serve as way to access UI or API freshly via auth with fixed password with no document persistence beyond that single session.
    :param enforce_h2ogpt_api_key: Whether to enforce h2oGPT token usage for API
    :param enforce_h2ogpt_ui_key: Whether to enforce h2oGPT token usage for UI (same keys as API assumed)
    :param h2ogpt_api_keys: list of tokens allowed for API access or file accessed on demand for json of list of keys
    :param h2ogpt_key: E.g. can be set when accessing gradio h2oGPT server from local gradio h2oGPT server that acts as client to that inference server
                       Only applied for API at runtime when API accesses using gradio inference_server are made
    :param extra_allowed_paths: List of strings for extra allowed paths users could access for file viewing/downloading.  '.' can be used but be careful what that exposes.
           Note by default all paths in langchain_mode_paths given at startup are allowed
    :param blocked_paths: Any blocked paths to add for gradio access for file viewing/downloading.

    :param max_max_time: Maximum max_time for gradio slider
    :param max_max_new_tokens: Maximum max_new_tokens for gradio slider
    :param min_max_new_tokens: Minimum of max_new_tokens, when auto-scaling down to handle more docs/prompt, but still let generation have some tokens
    :param max_input_tokens: Max input tokens to place into model context for each LLM call
                             -1 means auto, fully fill context for query, and fill by original document chunk for summarization
                             >=0 means use that to limit context filling to that many tokens
    :param max_total_input_tokens: like max_input_tokens but instead of per LLM call, applies across all LLM calls for single summarization/extraction action

    :param docs_token_handling: 'chunk' means fill context with top_k_docs (limited by max_input_tokens or model_max_len) chunks for query
                                                                     or top_k_docs original document chunks summarization
                                None or 'split_or_merge' means same as 'chunk' for query, while for summarization merges documents to fill up to max_input_tokens or model_max_len tokens

    :param docs_joiner: string to join lists of text when doing split_or_merge.  None means '\n\n'

    :param hyde_level: HYDE level for HYDE approach (https://arxiv.org/abs/2212.10496)
                 0: No HYDE
                 1: Use non-document-based LLM response and original query for embedding query
                 2: Use document-based LLM response and original query for embedding query
                 3+: Continue iterations of embedding prior answer and getting new response
    :param hyde_template:
                 None, 'None', 'auto' uses internal value and enable
                 '{query}' is minimal template one can pass
    :param hyde_show_only_final:  Whether to show only last result of HYDE, not intermediate steps
    :param hyde_show_intermediate_in_accordion: Whether to show intermediate HYDE, but inside HTML accordion
    :param map_reduce_show_intermediate_in_accordion: Whether to show intermediate map_reduce, but inside HTML accordion

    :param visible_models: Which models in model_lock list to show by default
           Takes integers of position in model_lock (model_states) list or strings of base_model names
           Ignored if model_lock not used
           For nochat API, this is single item within a list for model by name or by index in model_lock
                                If None, then just use first model in model_lock list
                                If model_lock not set, use model selected by CLI --base_model etc.
           Note that unlike h2ogpt_key, this visible_models only applies to this running h2oGPT server,
              and the value is not used to access the inference server.
              If need a visible_models for an inference server, then use --model_lock and group together.
    :param max_visible_models: maximum visible models to allow to select in UI

    :param visible_ask_anything_high: Whether ask anything block goes near top or near bottom of UI Chat
    :param visible_visible_models: Whether visible models drop-down is visible in UI
    :param visible_submit_buttons: whether submit buttons are visible when UI first comes up
    :param visible_side_bar: whether left side bar is visible when UI first comes up
    :param visible_document_subset: whether document subset is visible when UI first comes up
    :param visible_max_quality: whether max quality is visible when UI first comes up
    :param visible_add_doc_to_chat: whether add document to chat is visible when UI first comes up
    :param visible_chat_history: whether chat history being choosable is visible when UI first comes up
    :param visible_doc_track: whether left side bar's document tracking is visible when UI first comes up

    :param visible_chat_tab: "" for chat tab
    :param visible_doc_selection_tab:  "" for doc selection tab
    :param visible_doc_view_tab: "" for doc view tab
    :param visible_chat_history_tab: "" for chat history tab
    :param visible_expert_tab: "" for expert tab
    :param visible_models_tab: "" for models tab
    :param visible_system_tab: "" for system tab
    :param visible_tos_tab: "" for ToS tab
    :param visible_login_tab: "" for Login tab (needed for persistence or to enter key for UI access to models and ingestion)
    :param visible_hosts_tab: "" for hosts tab
    :param visible_langchain_action_radio: "" for action radio
    :param visible_langchain_purge: for purge option

    :param chat_tabless: Just show Chat as block without tab (useful if want only chat view)
    :param visible_h2ogpt_links: Whether github stars, URL are visible
    :param visible_h2ogpt_qrcode: Whether QR code is visible
    :param visible_h2ogpt_logo: Whether central logo is visible
    :param visible_chatbot_label: Whether to show label in chatbot (e.g. if only one model for own purpose, then can set to False)
    :param visible_all_prompter_models: Whether to show all prompt_type_to_model_name items or just curated ones
    :param visible_curated_models: Whether to show curated models (useful to see few good options)
    :param actions_in_sidebar: Whether to show sidebar with actions in old style
    :param document_choice_in_sidebar: Whether to show document choices in sidebar
           Useful if often changing picking specific document(s)
    :param enable_add_models_to_list_ui: Whether to show add model, lora, server to dropdown list
           Disabled by default since clutters Models tab in UI, and can just add custom item directly in dropdown
    :param max_raw_chunks: Maximum number of chunks to show in UI when asking for raw DB text from documents/collection
    :param pdf_height: Height of PDF viewer in UI
    :param avatars: Whether to show avatars in chatbot
    :param add_disk_models_to_ui: Whether to add HF cache models and llama.cpp models to UI
    :param page_title: Title of the web page, default is h2oGPT
    :param favicon_path: Path to favicon, default is h2oGPT favicon
    :param visible_ratings: Whether full review is visible, else just likable chatbots
    :param reviews_file: File to store reviews, set to `reviews.csv` if visible_ratings=True if this isn't set

    :param sanitize_user_prompt: whether to remove profanity from user input (slows down input processing)
      Requires optional packages:
      pip install alt-profanity-check==1.2.2 better-profanity==0.7.0
    :param sanitize_bot_response: whether to remove profanity and repeat lines from bot output (about 2x slower generation for long streaming cases due to better_profanity being slow)
    :param extra_model_options: extra models to show in list in gradio
    :param extra_lora_options: extra LORA to show in list in gradio
    :param extra_server_options: extra servers to show in list in gradio
    :param score_model: which model to score responses
           None: no response scoring
           'auto': auto mode, '' (no model) for CPU or 1 GPU, 'OpenAssistant/reward-model-deberta-v3-large-v2' for >=2 GPUs,
            because on CPU takes too much compute just for scoring response

    :param verifier_model: model for verifier
    :param verifier_tokenizer_base_model: tokenizer server for verifier (if empty/None, infer from model)
    :param verifier_inference_server: inference server for verifier

    :param eval_filename: json file to use for evaluation, if None is sharegpt
    :param eval_prompts_only_num: for no gradio benchmark, if using eval_filename prompts for eval instead of examples
    :param eval_prompts_only_seed: for no gradio benchmark, seed for eval_filename sampling
    :param eval_as_output: for no gradio benchmark, whether to test eval_filename output itself

    :param langchain_mode: Data source to include.  Choose "UserData" to only consume files from make_db.py.
           None: auto mode, check if langchain package exists, at least do LLM if so, else Disabled
           If not passed, then chosen to be first langchain_modes, else langchain_mode->Disabled is set if no langchain_modes either
           WARNING: wiki_full requires extra data processing via read_wiki_full.py and requires really good workstation to generate db, unless already present.
    :param user_path: user path to glob from to generate db for vector search, for 'UserData' langchain mode.
           If already have db, any new/changed files are added automatically if path set, does not have to be same path used for prior db sources
    :param langchain_modes: dbs to generate at launch to be ready for LLM
           Apart from additional user-defined collections, can include ['wiki', 'wiki_full', 'UserData', 'MyData', 'github h2oGPT', 'DriverlessAI docs']
             But wiki_full is expensive and requires preparation
           To allow personal space only live in session, add 'MyData' to list
           Default: If only want to consume local files, e.g. prepared by make_db.py, only include ['UserData']
           If have own user modes, need to add these here or add in UI.
    :param langchain_mode_paths: dict of langchain_mode keys and disk path values to use for source of documents
           E.g. "{'UserData2': 'userpath2'}"
           A disk path be None, e.g. --langchain_mode_paths="{'UserData2': None}" even if existing DB, to avoid new documents being added from that path, source links that are on disk still work.
           If `--user_path` was passed, that path is used for 'UserData' instead of the value in this dict
    :param langchain_mode_types: dict of langchain_mode keys and database types
           E.g. python generate.py --base_model=llama --langchain_modes=['TestData'] --langchain_mode_types="{'TestData':'shared'}"
           The type is attempted to be inferred if directory already exists, then don't have to pass this
    :param detect_user_path_changes_every_query: whether to detect if any files changed or added every similarity search (by file hashes).
           Expensive for large number of files, so not done by default.  By default only detect changes during db loading.
    :param update_selection_state_from_cli: whether to update all user options (during login) with CLI options for langchain_modes, langchain_mode_paths, langchain_mode_types
           If want user auth state to always be used regardless of changes to CLI options, then set False

    :param langchain_action: Mode langchain operations in on documents.
            Query: Make query of document(s)
            Summarize or Summarize_map_reduce: Summarize document(s) via map_reduce
            Summarize_all: Summarize document(s) using entire document at once
            Summarize_refine: Summarize document(s) using entire document, and try to refine before returning summary
            Extract: Extract information from document(s) via map (no reduce)

            Currently enabled is Query, Summarize, and Extract.

            Summarize is a "map reduce" and extraction is "map". That is, map returns a text output (roughly) per input item, while reduce reduces all maps down to single text output.
            The "roughly" refers to fact that if one has docs_token_handling='split_or_merge' then we split or merge chunks, so you will get a map for some optimal-sized chunks given the model size.  If you choose docs_token_handling='chunk', then you get back a map for each chunk you give, but you should ensure the model token limit is not exceeded yourself.

            Summarize is useful when wanting to reduce down to single text, while Extract is useful when want to operate the prompt on blocks of data and get back a result per block.

    :param langchain_agents: Which agents to use
            'search': Use Web Search as context for LLM response, e.g. SERP if have SERPAPI_API_KEY in env
    :param force_langchain_evaluate: Whether to force langchain LLM use even if not doing langchain, mostly for testing.

    :param visible_langchain_actions: Which actions to allow
    :param visible_langchain_agents: Which agents to allow

    :param document_subset: Default document choice when taking subset of collection
    :param document_choice: Chosen document(s) by internal name, 'All' means use all docs
        e.g. --document_choice="['file2.pdf']" or --document_choice="['file2.pdf', 'file3.pdf']"
    :param document_source_substrings: substrings in list to search in source names in metadata for chroma dbs
    :param document_source_substrings_op: 'and or 'or' for source search words
    :param document_content_substrings: substrings in list to search in content for chroma dbs
    :param document_content_substrings_op: 'and or 'or' for content search words

    :param use_llm_if_no_docs: Whether to use LLM even if no documents, when langchain_mode=UserData or MyData or custom
    :param load_db_if_exists: Whether to load chroma db if exists or re-generate db
    :param keep_sources_in_context: Whether to keep url sources in context, not helpful usually
    :param db_type: 'faiss' for in-memory
                    'chroma' (for chroma >= 0.4)
                    'chroma_old' (for chroma < 0.4) -- recommended for large collections
                    'weaviate' for persisted on disk
                    'qdrant' for a Qdrant server or an in-memory instance
    :param use_openai_embedding: Whether to use OpenAI embeddings for vector db
    :param use_openai_model: Whether to use OpenAI model for use with vector db
    :param hf_embedding_model: Which HF embedding model to use for vector db
           Default is instructor-large with 768 parameters per embedding if have GPUs, else all-MiniLM-L6-v2 if no GPUs
           Can also choose simpler model with 384 parameters per embedding: "sentence-transformers/all-MiniLM-L6-v2"
           A better choice is: 'BAAI/bge-large-en-v1.5'
           For multilingual can use intfloat/multilingual-e5-large
           We support automatically changing of embeddings for chroma, with a backup of db made if this is done
    :param migrate_embedding_model: whether to use hf_embedding_model embedding even if database already had an embedding set.
           used to migrate all embeddings to a new one, but will take time to re-embed.
           Default (False) is to use the prior embedding for existing databases, and only use hf_embedding_model for new databases
           If had old database without embedding saved, then hf_embedding_model is also used.
    :param cut_distance: Distance to cut off references with larger distances when showing references.
           1.64 is good to avoid dropping references for all-MiniLM-L6-v2, but instructor-large will always show excessive references.
           For all-MiniLM-L6-v2, a value of 1.5 can push out even more references, or a large value of 100 can avoid any loss of references.
    :param answer_with_sources: Whether to determine (and return) sources
    :param append_sources_to_answer: Whether to place source information in chat response (ignored by LLM).  Always disabled for API.
    :param append_sources_to_chat: Whether to place sources information in chat response but in separate chat turn (ignored by LLM).  Always disabled for API.
    :param sources_show_text_in_accordion: whether to show accordion for document references in chatbot UI
    :param top_k_docs_max_show: Max number of docs to show in UI for sources
           If web search is enabled, then this is modified to be max(top_k_docs_max_show, number of links used in search)
    :param show_link_in_sources: Whether to show URL link to source document in references
    :param langchain_instruct_mode: Whether to have langchain operate in instruct mode (True) or few-shot mode (False)
           Normally this might be decidable from --prompt_type=plain, but in some cases (like vllm_chat) we want inference server to handle all prompting, so need to tell h2oGPT to use plain prompting, but don't want to change langchain behavior

    :param pre_prompt_query: prompt before documents to query, if None then use internal defaults
    :param prompt_query: prompt after documents to query, if None then use internal defaults
    :param pre_prompt_summary: prompt before documents to summarize/extract from, if None then use internal defaults
    :param prompt_summary: prompt after documents to summarize/extract from, if None then use internal defaults
           For summarize/extract, normal to have empty query (nothing added in ask anything in UI or empty string in API)
           If pass query, template is "Focusing on %s, %s" % (query, prompt_summary)
           If pass query and iinput, template is "Focusing on %s, %s, %s" % (query, iinput, prompt_summary)

    For query, prompt template is:
      "{pre_prompt_query}
        \"\"\"
        {fstring}
        \"\"\"
        {prompt_query}{instruction}"
    For summarization or extraction, for some internal document part fstring, the template looks like:
        "{pre_prompt_summary}
        \"\"\"
        {fstring}
        \"\"\"
        {prompt_summary}"
     If added instruction for summarization or extraction, prompt template is
      "{pre_prompt_summary}
        \"\"\"
        {fstring}
        \"\"\"
        Focusing on {instruction}, {prompt_summary}"
        
    {fstring} is some document chunks separated by {docs_joiner}

    :param hyde_llm_prompt: hyde prompt for first step when using LLM

    :param all_docs_start_prompt: Prompt before all documents
    :param all_docs_finish_prompt: Prompt after all documents

    :param user_prompt_for_fake_system_prompt: user part of pre-conversation if LLM doesn't handle system prompt
    :param json_object_prompt: prompt for getting LLM to do JSON object
    :param json_object_prompt_simpler: simpler of "" for MistralAI
    :param json_code_prompt: prompt for getting LLm to do JSON in code block
    :param json_code_prompt_if_no_schema: prompt part for LLM if not schema, but need good keys etc. for JSON (e.g. due to Claude-3 limitations)
    :param json_schema_instruction: prompt for LLM to use schema
    :param json_preserve_system_prompt: whether to preserve system_prompt for JSON mode
    :param json_object_post_prompt_reminder: json object reminder about JSON
    :param json_code_post_prompt_reminder: json code w/ schema reminder about JSON
    :param json_code2_post_prompt_reminder: json code wo/ schema reminder about JSON

    :param doc_json_mode: Use system prompting approach with JSON input and output, e.g. for codellama or GPT-4
    :param metadata_in_context: Keys of metadata to include in LLM context for Query
           'all': Include all metadata
           'auto': Includes these keys: ['date', 'file_path', 'input_type', 'keywords', 'chunk_id', 'page', 'source', 'title', 'total_pages']
           ['key1', 'key2', ...]: Include only these keys
            NOTE: not all parsers have all keys, only keys that exist are added to each document chunk.
           Example key-values that some PDF parsers make:
                author = Zane Durante, Bidipta Sarkar, Ran Gong, Rohan Taori, Yusuke Noda, Paul Tang, Ehsan Adeli, Shrinidhi Kowshika Lakshmikanth, Kevin Schulman, Arnold Milstein, Demetri Terzopoulos, Ade Famoti, Noboru Kuno, Ashley Llorens, Hoi Vo, Katsu Ikeuchi, Li Fei-Fei, Jianfeng Gao, Naoki Wake, Qiuyuan Huang
                chunk_id = 21
                creationDate = D:20240209020045Z
                creator = LaTeX with hyperref
                date = 2024-02-11 23:58:11.929155
                doc_hash = 5db1d548-7
                file_path = /tmp/gradio/15ac25af8610f21b9ab55252f1944841727ba157/2402.05929.pdf
                format = PDF 1.5
                hashid = 3cfb31cea127c745c72554f4714105dd
                head = An Interactive Agent Foundation Model
                Figure 2. We
                input_type = .pdf
                keywords = Machine Learning, ICML
                modDate = D:20240209020045Z
                order_id = 2
                page = 2
                parser = PyMuPDFLoader
                producer = pdfTeX-1.40.25
                source = /tmp/gradio/15ac25af8610f21b9ab55252f1944841727ba157/2402.05929.pdf
                subject = Proceedings of the International Conference on Machine Learning 2024
                time = 1707724691.929157
                title = An Interactive Agent Foundation Model
                total_pages = 22

    :param add_chat_history_to_context: Include chat context when performing action
           Not supported when using CLI mode
    :param add_search_to_context: Include web search in context as augmented prompt
    :param context: Default context to use (for system pre-context in gradio UI)
           context comes before chat_conversation and any document Q/A from text_context_list
    :param iinput: Default input for instruction-based prompts
    :param allow_upload_to_user_data: Whether to allow file uploads to update shared vector db (UserData or custom user dbs)
           Ensure pass user_path for the files uploaded to be moved to this location for linking.
    :param reload_langchain_state: Whether to reload langchain_modes.pkl file that contains any new user collections.
    :param allow_upload_to_my_data: Whether to allow file uploads to update personal vector db
    :param enable_url_upload: Whether to allow upload from URL
    :param enable_text_upload: Whether to allow upload of text
    :param enable_sources_list: Whether to allow list (or download for non-shared db) of list of sources for chosen db
    :param chunk: Whether to chunk data (True unless know data is already optimally chunked)
    :param chunk_size: Size of chunks, with typically top-4 passed to LLM, so needs to be in context length
    :param top_k_docs: For langchain_action query: number of chunks to give LLM
                       -1 : auto-fills context up to max_seq_len
                       For langchain_action summarize/extract: number of document parts, like pages for PDF.
                       There's no such thing as chunks for summarization.
                       -1 : auto-fills context up to max_seq_len
    :param docs_ordering_type:
        Type of ordering of docs.
        'best_first': Order by score so score is worst match near prompt
        'best_near_prompt' or 'reverse_sort' : reverse docs order so most relevant is closest to question.
           Best choice for sufficiently smart model, and truncation occurs for oldest context, so best then too.
           But smaller 6_9 models fail to use newest context and can get stuck on old information.
        '' or None (i.e. default) or 'reverse_ucurve_sort' : Sort so most relevant is either near start or near end
           Best to avoid "lost in middle" as well as avoid hallucinating off starting content that LLM focuses on alot.
    :param auto_reduce_chunks: Whether to automatically reduce top_k_docs to fit context given prompt
    :param max_chunks: If top_k_docs=-1, maximum number of chunks to allow
    :param headsize: Maximum number of characters for head of document document for UI to show
    :param n_jobs: Number of processors to use when consuming documents (-1 = all, is default)
    :param n_gpus: Number of GPUs (None = autodetect)
    :param clear_torch_cache_level: 0: never clear except where critically required
                                    1: clear critical
                                    2: clear aggressively and clear periodically every 20s to free-up GPU memory (may lead to lag in response)

    :param use_unstructured: Enable unstructured URL loader
    :param use_playwright: Enable PlayWright URL loader
    :param use_selenium: Enable Selenium URL loader
    :param use_scrapeplaywright: Enable Scrape PlayWright URL loader
    :param use_scrapehttp: Enable Scrape HTTP URL loader using aiohttp

    :param use_pymupdf: enable PyMUPDF 'auto' means use first, use others if they are 'auto' if no result
    :param use_unstructured_pdf: enable Unstructured PDF loader, 'auto' means use if pymupdf fails to get doc result
    :param use_pypdf: enable PyPDF loader 'auto' means use if unstructured fails to get doc result
    :param enable_pdf_ocr: 'auto' means only use OCR if normal text extraction fails.  Useful for pure image-based PDFs with text.
                                  if enable_pdf_doctr == 'on' then don't do.
                            'on' means always do OCR as additional parsing of same documents
                            'off' means don't do OCR (e.g. because it's slow even if 'auto' only would trigger if nothing else worked)
    :param enable_pdf_doctr: Whether to support doctr on pdfs, 'auto' means use do if failed to get doc result so far
    :param try_pdf_as_html: Try "PDF" as if HTML file, in case web link has .pdf extension but really is just HTML

    :param enable_ocr: Whether to support OCR on images
    :param enable_doctr: Whether to support doctr on images (using OCR better than enable_ocr=True)
    :param enable_pix2struct: Whether to support pix2struct on images for captions
    :param enable_captions: Whether to support captions for image files as documents,
           then preloads that model if pre_load_image_audio_models=True
    :param enable_llava: If LLaVa IP port is set, whether to use response for image ingestion
    :param enable_transcriptions: Whether to enable audio transcriptions (youtube of from files)
           Preloaded if pre_load_image_audio_models=True

    :param pre_load_image_audio_models: Whether to preload caption model (True), or load after forking parallel doc loader (False)
           parallel loading disabled if preload and have images, to prevent deadlocking on cuda context
           Recommended if using larger caption model or doing production serving with many users to avoid GPU OOM if many would use model at same time
           Also applies to DocTR and ASR models

    :param captions_model: Which model to use for captions.
           captions_model: str = "microsoft/Florence-2-base",  # fine
           captions_model: str = "microsoft/Florence-2-large",   # quite good
    :param caption_gpu: If support caption, then use GPU if exists
    :param caption_gpu_id: Which GPU id to use, if 'auto' then select 0

    :param doctr_gpu: If support doctr, then use GPU if exists
    :param doctr_gpu_id: Which GPU id to use, if 'auto' then select 0

    :param llava_model:  IP:port for h2oai version of LLaVa gradio server for hosted image chat
           E.g. http://192.168.1.46:7861
           None means no such LLaVa support
    :param llava_prompt: Prompt passed to LLaVa for querying the image

    :param image_file: Initial image for UI (or actual image for CLI) Vision Q/A.  Or list of images for some models
    :param image_control: Initial image for UI Image Control
    :param images_num_max: Maximum number of images in any LLM call.
        if None, then checks images_num_max and uses that value for defined models (assumes 80GB GPU), else uses 1
        If set here or in model_lock, then that model uses the set value
        If set to 0, then won't use images even if image model and given images
        If set to -1, then always forces batching if any images, even if model could handle all images at once. The amount is inferred for each model
           This is useful because models do poorly when mixing images and text when text duplicates content of image information, LLM tends to just look at text not image even if image contains better information.
        If set to -2, -3, etc., then 1, 2, 3 images are used per batch
    :param image_resolution: Resolution of any images
    :param image_format: Preferred format of images, esp. for video output
    :param rotate_align_resize_image: Whether to apply rotation, alignment, resize before giving to LLM
    :param video_frame_period: Period of frames to use from video
    :param image_batch_image_prompt: Prompt used to query image only if doing batching of images
    :param image_batch_final_prompt: Prompt used to query result of batching of images
    :param image_batch_stream: Whether to stream batching of images.
    :param visible_vision_models: Model to use for vision, e.g. if base LLM has no vision
    :param video_file: Video file for gradio to start with

    :param response_format: text or json_object or json_code
        json_object means always try to use best mechanism to make JSON.
        json_code means use code block method, not guided_json or built-in json mode
    # https://github.com/vllm-project/vllm/blob/a3c226e7eb19b976a937e745f3867eb05f809278/vllm/entrypoints/openai/protocol.py#L117-L135
    :param guided_json: str or dict of JSON schema
    :param guided_regex:
    :param guided_choice: list of strings to have LLM choose from
    :param guided_grammar:
    :param guided_whitespace_pattern:

    :param asr_model: Name of model for ASR, e.g. openai/whisper-medium or openai/whisper-large-v3 or distil-whisper/distil-large-v3 or microsoft/speecht5_asr
           whisper-medium uses about 5GB during processing, while whisper-large-v3 needs about 10GB during processing
    :param asr_gpu: Whether to use GPU for ASR model
    :param asr_gpu_id: Which GPU to put ASR model on (only used if preloading model)
    :param asr_use_better: Whether to use BetterTransformer
    :param asr_use_faster: Whether to use faster_whisper package and models (loads normal whisper then unloads it, to get this into pipeline)

    :param enable_stt: Whether to enable and show Speech-to-Text (STT) with microphone in UI
         Note STT model is always preloaded, but if stt_model=asr_model and pre_load_image_audio_models=True, then asr model is used as STT model.
    :param stt_model: Name of model for STT, can be same as asr_model, which will then use same model for conserving GPU
    :param stt_gpu: Whether to use gpu for STT model
    :param stt_gpu_id: If not using asr_model, then which GPU to go on if using cuda
    :param stt_continue_mode: How to continue speech with button control
           0: Always append audio regardless of start/stop of recording, so always appends in STT model for full STT conversion
              Only can edit after hit stop and then submit, if hit record again edits are lost since using only audio stream for STT conversion
           1: If hit stop, text made so far is saved and audio cleared, so next recording will be separate text conversion
              Can make edits on any text after hitting stop and they are preserved

    :param enable_tts: Whether to enable TTS
    :param tts_gpu: Whether to use GPU if present for TTS
    :param tts_gpu_id: Which GPU ID to use for TTS
    :param tts_model: Which model to use.
                   For microsoft, use 'microsoft/speecht5_tts'
                   For coqui.ai use one given by doing in python:
                   ```python
                   from tts_coqui import list_models
                   list_models()
                   ```
                   e.g. 'tts_models/multilingual/multi-dataset/xtts_v2'

                   Note that coqui.ai models are better, but some have non-commercial research license, while microsoft models are MIT.
                   So coqui.ai ones can be used for non-commercial activities only, and one should agree to their license, see: https://coqui.ai/cpml
                   Commercial use of xtts_v2 should be obtained through their product offering at https://coqui.ai/

    :param tts_gan_model: For microsoft model, which gan model to use, e.g. 'microsoft/speecht5_hifigan'
    :param tts_coquiai_deepspeed: For coqui.ai models, whether to use deepspeed for faster inference
        Disabled by default, saw compilation hang recently
    :param tts_coquiai_roles: role dictionary mapping name (key) to wave file (value)
           If None, then just use default from get_role_to_wave_map()

    :param chatbot_role: Default role for coqui models.  If 'None', then don't by default speak when launching h2oGPT for coqui model choice.
    :param speaker: Default speaker for microsoft models  If 'None', then don't by default speak when launching h2oGPT for microsoft model choice.
    :param tts_language: Default language for coqui models
    :param tts_speed: Default speed of TTS, < 1.0 (needs rubberband) for slower than normal, > 1.0 for faster.  Tries to keep fixed pitch.
    :param tts_action_phrases: Phrases or words to use as action word to trigger click of Submit hands-free assistant style
           Set to None or empty list to avoid any special action words
    :param tts_stop_phrases:  Like tts_action_phrases but to stop h2oGPT from speaking and generating

            NOTE: Action/Stop phrases should be rare but easy (phonetic) words for Whisper to recognize.
                  E.g. asking GPT-4 a couple good ones are ['Nimbus'] and ['Yonder'],
                  and one can help Whisper by saying "Nimbus Clouds" which still works as "stop word" as trigger.

    :param sst_floor: Floor in wave square amplitude below which ignores the chunk of audio
                      This helps avoid long silence messing up the transcription.

    :param jq_schema: control json loader
           By default '.[]' ingests everything in brute-force way, but better to match your schema
           See: https://python.langchain.com/docs/modules/data_connection/document_loaders/json#using-jsonloader

    :param extract_frames: How many unique frames to extract from video (if 0, then just do audio if audio type file as well)

    :param enable_image: Whether to enable image generation model
    :param visible_image_models: Which image gen models to include
    :param image_size
    :param image_quality
    :param image_guidance_scale
    :param image_num_inference_steps
    :param image_gpu_ids: GPU ids to use for each visible image model

    :param enable_llava_chat: Whether to use LLaVa model to chat directly against instead of just for ingestion

    :param max_quality: Choose maximum quality ingestion with all available parsers
           Pro: Catches document when some default parsers would fail
           Pro: Enables DocTR that has much better OCR than Tesseract
           Con: Fills DB with results from all parsers, so similarity search gives redundant results

    :param enable_heap_analytics: Toggle telemetry.
    :param heap_app_id: App ID for Heap, change to your ID.

    :param cert_lookup_directory: Defines the directory containing the additional private certs to trust.
    :return:
    """

    append_certificates(cert_lookup_directory)

    main_kwargs = locals().copy()

    if base_model is None:
        base_model = ''
    if tokenizer_base_model is None:
        tokenizer_base_model = ''
    if lora_weights is None:
        lora_weights = ''
    if inference_server is None:
        inference_server = ''

    # listen to env if set
    model_lock = os.getenv('model_lock', str(model_lock))
    model_lock = ast.literal_eval(model_lock)

    chat_conversation = str_to_list(chat_conversation)
    text_context_list = str_to_list(text_context_list)
    llamacpp_dict = str_to_dict(llamacpp_dict)
    tts_coquiai_roles = str_to_dict(tts_coquiai_roles)
    roles_state0 = tts_coquiai_roles
    tts_action_phrases = str_to_list(tts_action_phrases)
    tts_stop_phrases = str_to_list(tts_stop_phrases)
    visible_image_models = str_to_list(visible_image_models)
    if not image_size:
        image_size = image_size_default
    image_gpu_ids = str_to_list(image_gpu_ids)
    document_choice = str_to_list(document_choice)
    visible_models = str_to_list(visible_models, allow_none=True)  # None means first model
    visible_vision_models = str_to_list(visible_vision_models, allow_none=True)  # None means first model
    if image_gpu_ids:
        assert len(image_gpu_ids) == len(visible_image_models)
    if isinstance(metadata_in_context, str) and metadata_in_context == 'None':
        metadata_in_context = []
    if seed is None:
        seed = 0
    if image_batch_image_prompt is None:
        image_batch_image_prompt = image_batch_image_prompt0
    if image_batch_final_prompt is None:
        image_batch_final_prompt = image_batch_final_prompt0

    assert response_format in response_formats, "Invalid response_format: %s, must be in %s" % (
        response_format, response_formats)
    assert isinstance(guided_json, (str, dict, type(None)))
    assert isinstance(guided_regex, (type(None), str))
    assert isinstance(guided_choice, (type(None), list))
    assert isinstance(guided_grammar, (type(None), str))
    assert isinstance(guided_whitespace_pattern, (type(None), str))

    # defaults, but not keep around if not used so can use model_path_llama for prompt_type auto-setting
    # NOTE: avoid defaults for model_lock, require to be specified
    if base_model == 'llama':
        if not model_path_llama:
            model_path_llama = 'https://huggingface.co/TheBloke/Llama-2-7b-Chat-GGUF/resolve/main/llama-2-7b-chat.Q6_K.gguf?download=true'
            prompt_type = 'llama2'
        if not prompt_type:
            prompt_type = 'unknown'
    elif base_model == 'gptj' and not model_name_gptj:
        model_name_gptj = 'ggml-gpt4all-j-v1.3-groovy.bin'
    elif base_model == 'gpt4all_llama' and not model_name_gpt4all_llama:
        model_name_gpt4all_llama = 'ggml-wizardLM-7B.q4_2.bin'
    if load_exllama and not model_name_exllama_if_no_config:
        model_name_exllama_if_no_config = 'TheBloke/Nous-Hermes-Llama2-GPTQ'

    # switch-a-roo on base_model so can pass GGUF/GGML as base model
    base_model0 = base_model  # for prompt infer
    base_model, model_path_llama, load_gptq, load_awq, llamacpp_dict['n_gqa'] = \
        switch_a_roo_llama(base_model, model_path_llama, load_gptq, load_awq,
                           llamacpp_dict.get('n_gqa', 0), llamacpp_path)

    # add others to single dict
    llamacpp_dict['model_path_llama'] = model_path_llama
    llamacpp_dict['model_name_gptj'] = model_name_gptj
    llamacpp_dict['model_name_gpt4all_llama'] = model_name_gpt4all_llama
    llamacpp_dict['model_name_exllama_if_no_config'] = model_name_exllama_if_no_config
    # ensure not used by accident
    del model_path_llama
    del model_name_gptj
    del model_name_gpt4all_llama
    del model_name_exllama_if_no_config
    # if user overrides but doesn't set these:
    if 'n_batch' not in llamacpp_dict:
        llamacpp_dict['n_batch'] = 128
    if 'n_gpu_layers' not in llamacpp_dict:
        llamacpp_dict['n_gpu_layers'] = 100
    if 'n_gqa' not in llamacpp_dict:
        llamacpp_dict['n_gqa'] = 0

    exllama_dict = str_to_dict(exllama_dict)
    gptq_dict = str_to_dict(gptq_dict)
    sink_dict = str_to_dict(sink_dict)
    hf_model_dict = str_to_dict(hf_model_dict)

    enable_imagegen = enable_image and \
                      len(set(visible_image_models).difference(valid_imagegen_models)) < len(set(visible_image_models))
    enable_imagechange = enable_image and \
                         len(set(visible_image_models).difference(valid_imagechange_models)) < len(
        set(visible_image_models))
    enable_imagestyle = enable_image and \
                        len(set(visible_image_models).difference(valid_imagestyle_models)) < len(
        set(visible_image_models))

    if agent_server and not have_autogen:
        print("Disabled Agent Server since no Agent package installed")
        agent_server = False

    if os.environ.get('SERPAPI_API_KEY') is None and \
            LangChainAgent.SEARCH.value in visible_langchain_agents:
        visible_langchain_agents.remove(LangChainAgent.SEARCH.value)
    if (not have_diffusers or not enable_imagegen) and \
            LangChainAction.IMAGE_GENERATE.value in visible_langchain_actions:
        visible_langchain_actions.remove(LangChainAction.IMAGE_GENERATE.value)
    if (not have_diffusers or not enable_imagechange) and \
            LangChainAction.IMAGE_CHANGE.value in visible_langchain_actions:
        visible_langchain_actions.remove(LangChainAction.IMAGE_CHANGE.value)
    if (not have_diffusers or not enable_imagestyle) and \
            LangChainAction.IMAGE_STYLE.value in visible_langchain_actions:
        visible_langchain_actions.remove(LangChainAction.IMAGE_STYLE.value)
    if (not llava_model or not enable_llava or not enable_llava_chat) and \
            LangChainAction.IMAGE_QUERY.value in visible_langchain_actions:
        visible_langchain_actions.remove(LangChainAction.IMAGE_QUERY.value)

    if model_lock:
        assert gradio or function, "model_lock only supported for gradio=True or function=True"
        assert not cli, "model_lock only supported for cli=False"
        assert not (not cli and not (gradio or function)), "model_lock only supported for eval (cli=gradio=False)"
        assert not base_model, "Don't specify model_lock and base_model"
        assert not tokenizer_base_model, "Don't specify model_lock and tokenizer_base_model"
        assert not lora_weights, "Don't specify model_lock and lora_weights"
        assert not inference_server, "Don't specify model_lock and inference_server"
        # assert not prompt_type, "Don't specify model_lock and prompt_type"
        # assert not prompt_dict, "Don't specify model_lock and prompt_dict"

        if gradio_ui_stream_chunk_size is None:
            gradio_ui_stream_chunk_size = 20
    else:
        # for faster default feel of speed
        if gradio_ui_stream_chunk_size is None:
            gradio_ui_stream_chunk_size = 0

    n_jobs = int(os.getenv('n_jobs', str(n_jobs)))
    is_hf = bool(int(os.getenv("HUGGINGFACE_SPACES", '0')))
    is_gpth2oai = bool(int(os.getenv("GPT_H2O_AI", '0')))
    is_public = is_hf or is_gpth2oai  # multi-user case with fixed model and disclaimer
    if enforce_h2ogpt_ui_key is None:
        # nominally allow UI access public or not
        enforce_h2ogpt_ui_key = False
    if is_public:
        if max_visible_models is None and (gradio or function):
            is_gradio_h2oai = get_is_gradio_h2oai()
            max_visible_models = 4 if is_gradio_h2oai else None
        visible_hosts_tab = False
        visible_tos_tab = True
        if enforce_h2ogpt_api_key is None:
            enforce_h2ogpt_api_key = True
    else:
        if enforce_h2ogpt_api_key is None:
            enforce_h2ogpt_api_key = False
    if isinstance(h2ogpt_api_keys, str) and not os.path.isfile(h2ogpt_api_keys):
        h2ogpt_api_keys = str_to_list(h2ogpt_api_keys)
    os.environ['H2OGPT_H2OGPT_API_KEYS'] = str(h2ogpt_api_keys)
    if isinstance(extra_allowed_paths, str):
        extra_allowed_paths = str_to_list(extra_allowed_paths)
    if memory_restriction_level is None:
        memory_restriction_level = 2 if is_hf else 0  # 2 assumes run on 24GB consumer GPU
    else:
        assert 0 <= memory_restriction_level <= 3, "Bad memory_restriction_level=%s" % memory_restriction_level
    if n_jobs == -1:
        # if -1, assume hypercores, don't use, force user to pass n_jobs to be specific if not standard cores
        n_jobs = max(1, os.cpu_count() // 2)
    if is_public and os.getenv('n_jobs') is None:
        n_jobs = min(n_jobs, max(1, min(os.cpu_count() // 2, 8)))
    if is_public:
        gradio_upload_to_chatbot_num_max = 1
    if admin_pass is None:
        admin_pass = os.getenv("ADMIN_PASS")
    # will sometimes appear in UI or sometimes actual generation, but maybe better than empty result
    # but becomes unrecoverable sometimes if raise, so just be silent for now
    raise_generate_gpu_exceptions = True

    rope_scaling = str_to_dict(rope_scaling)

    if isinstance(auth, str):
        if auth.strip().startswith('['):
            auth = str_to_list(auth)
    if isinstance(auth, str) and auth:
        auth_filename = auth
    if not auth_filename:
        auth_filename = "auth.db"
    assert isinstance(auth, (str, list, tuple, type(None))), "Unknown type %s for auth=%s" % (type(auth), auth)
    if auth_filename.endswith('.db'):
        # this migrates json to db
        assert fetch_user(auth_filename, '', verbose=verbose) == {}

    if guest_name is None:
        if auth_access == 'closed':
            # ensure, but should be protected inside anyways
            guest_name = ''
        elif auth_access == 'open':
            guest_name = "guest"

    h2ogpt_pid = os.getpid() if close_button and not is_public else None

    # allow set token directly
    if not use_auth_token:
        use_auth_token = os.environ.get("HUGGING_FACE_HUB_TOKEN", use_auth_token)
    if isinstance(use_auth_token, str) and use_auth_token:
        os.environ['HUGGING_FACE_HUB_TOKEN'] = use_auth_token
    allow_upload_to_user_data = bool(
        int(os.environ.get("allow_upload_to_user_data", str(int(allow_upload_to_user_data)))))
    allow_upload_to_my_data = bool(int(os.environ.get("allow_upload_to_my_data", str(int(allow_upload_to_my_data)))))
    height = int(os.environ.get("HEIGHT", height))
    h2ocolors = bool(int(os.getenv('h2ocolors', h2ocolors)))

    # allow enabling langchain via ENV
    # FIRST PLACE where LangChain referenced, but no imports related to it
    langchain_modes = ast.literal_eval(os.environ.get("langchain_modes", str(langchain_modes)))
    if not isinstance(langchain_modes, list):
        langchain_modes = []
    # always allow DISABLED
    if LangChainMode.DISABLED.value not in langchain_modes:
        langchain_modes.append(LangChainMode.DISABLED.value)
    if not have_langchain:
        # only allow disabled, not even LLM that is langchain related
        langchain_mode = LangChainMode.DISABLED.value
        langchain_modes = [langchain_mode]

    # update
    langchain_mode_paths = str_to_dict(langchain_mode_paths)
    langchain_mode_types = str_to_dict(langchain_mode_types)
    for lmode in [LangChainMode.GITHUB_H2OGPT.value,
                  LangChainMode.H2O_DAI_DOCS.value,
                  LangChainMode.WIKI.value,
                  LangChainMode.WIKI_FULL.value,
                  ]:
        if lmode not in langchain_mode_types:
            langchain_mode_types[lmode] = 'shared'
    if lmode not in langchain_mode_paths:
        langchain_mode_types[lmode] = ''
    if user_path:
        user_path = makedirs(user_path, use_base=True)
        langchain_mode_paths['UserData'] = user_path
        langchain_mode_paths['UserData'] = LangChainTypes.SHARED.value
    if llamacpp_path:
        llamacpp_path = makedirs(llamacpp_path, use_base=True)

    if is_public:
        allow_upload_to_user_data = False
        if LangChainMode.USER_DATA.value in langchain_modes:
            langchain_modes.remove(LangChainMode.USER_DATA.value)
    if max_raw_chunks is None:
        max_raw_chunks = 30 if is_public else 1000000

    # in-place, for non-scratch dbs
    if allow_upload_to_user_data:
        # always listen to CLI-passed user_path if passed
        if user_path:
            langchain_mode_paths['UserData'] = user_path

    assert langchain_action in langchain_actions, "Invalid langchain_action %s not in %s" % (
        langchain_action, langchain_actions)
    assert len(
        set(langchain_agents).difference(langchain_agents_list)) == 0, "Invalid langchain_agents %s" % langchain_agents

    # auto-set langchain_mode
    langchain_mode = os.environ.get("LANGCHAIN_MODE", langchain_mode)
    if have_langchain and langchain_mode is None:
        # start in chat mode, in case just want to chat and don't want to get "No documents to query" by default.
        if LangChainMode.LLM.value in langchain_modes:
            langchain_mode = LangChainMode.LLM.value
        elif len(langchain_modes) >= 1:
            # infer even if don't pass which langchain_mode, just langchain_modes.
            langchain_mode = langchain_modes[0]
        if allow_upload_to_user_data and not is_public and langchain_mode_paths['UserData']:
            if verbose:
                print("Auto set langchain_mode=%s.  Could use UserData instead." % langchain_mode, flush=True)
        elif allow_upload_to_my_data:
            if verbose:
                print("Auto set langchain_mode=%s.  Could use MyData instead."
                      "  To allow UserData to pull files from disk,"
                      " set user_path or langchain_mode_paths, and ensure allow_upload_to_user_data=True" % langchain_mode,
                      flush=True)
        else:
            raise RuntimeError("Please pass --langchain_mode=<chosen mode> out of %s" % langchain_modes)
    if not have_langchain and langchain_mode not in [None, LangChainMode.DISABLED.value, LangChainMode.LLM.value]:
        raise RuntimeError("Asked for LangChain mode but langchain python package cannot be found.")
    if langchain_mode is None:
        # if not set yet, disable
        langchain_mode = LangChainMode.DISABLED.value
        print("Auto set langchain_mode=%s  Have langchain package: %s" % (langchain_mode, have_langchain), flush=True)
    # go ahead and add
    if langchain_mode not in langchain_modes:
        langchain_modes.append(langchain_mode)

    if is_public:
        # See also get_minmax_top_k_docs()
        # as another restriction apart from top_k_docs and when using long context models
        # model will limit more if required
        max_input_tokens = max_input_tokens_public if max_input_tokens is None else max_input_tokens
        max_total_input_tokens = max_total_input_tokens_public if max_total_input_tokens is None else max_total_input_tokens
        allow_upload_to_user_data = False
        input_lines = 1  # ensure set, for ease of use
        temperature = 0.3 if temperature is None else temperature
        top_p = 1.0 if top_p is None else top_p
        top_k = 1 if top_k is None else top_k
        penalty_alpha = 0.0 if penalty_alpha is None else penalty_alpha
        if is_hf:
            do_sample = True if do_sample is None else do_sample
            top_k_docs = 3 if top_k_docs is None else top_k_docs
        else:
            # by default don't sample, too chatty
            do_sample = False if do_sample is None else do_sample
            # now 10 since also limiting total tokens, in case some pages (for summarization) are small
            top_k_docs = max_top_k_docs_public if top_k_docs is None else top_k_docs

        if memory_restriction_level == 2:
            if not base_model and not inference_server and not model_lock:
                base_model = 'h2oai/h2ogpt-oasst1-512-12b'
                # don't set load_8bit if passed base_model, doesn't always work so can't just override
                load_8bit = True
                load_4bit = False  # FIXME - consider using 4-bit instead of 8-bit
        elif not inference_server:
            top_k_docs = max_top_k_docs_public if top_k_docs is None else top_k_docs
    if memory_restriction_level >= 2:
        load_8bit = True
        load_4bit = False  # FIXME - consider using 4-bit instead of 8-bit
        if hf_embedding_model is None:
            hf_embedding_model = "sentence-transformers/all-MiniLM-L6-v2"
        top_k_docs = 3 if top_k_docs is None else top_k_docs
    if top_k_docs is None:
        top_k_docs = max_top_k_docs_default
    if max_input_tokens is None:
        max_input_tokens = -1
    if max_total_input_tokens is None:
        max_total_input_tokens = -1
    if is_public:
        if not max_time:
            max_time = 60 * 2
        if not max_max_time:
            max_max_time = max_time
        if not max_new_tokens:
            max_new_tokens = 1024
        if not max_max_new_tokens:
            max_max_new_tokens = 1024
    else:
        if not max_max_time:
            max_max_time = 60 * 20
        if not max_max_new_tokens:
            max_max_new_tokens = 1024
    if is_hf:
        # must override share if in spaces
        share = False
        if not max_time:
            max_time = 60 * 1
        if not max_max_time:
            max_max_time = max_time
        # HF accounted for later in get_max_max_new_tokens()
    save_dir = os.getenv('SAVE_DIR', save_dir)
    save_dir = makedirs(save_dir, exist_ok=True, tmp_ok=True, use_base=True)
    score_model = os.getenv('SCORE_MODEL', score_model)
    if str(score_model) == 'None':
        score_model = ''
    # prioritize verifier model to replace output
    if verifier_model:
        score_model = ''
    all_inference_server = inference_server or model_lock and all(x.get('inference_server') for x in model_lock)
    if inference_server == 'openai' and base_model in openai_gpts:
        # deprecate chat models with non-chat API
        inference_server = 'openai_chat'

    if os.getenv('CONCURRENCY_COUNT'):
        concurrency_count = int(os.getenv('CONCURRENCY_COUNT'))
    elif concurrency_count:
        pass
    else:
        if all_inference_server:
            concurrency_count = 64
        else:
            # can't share LLM state across user requests due to k-v cache for LLMs
            # FIXME: In gradio 4 could use 1 for only LLM tasks, higher for rest
            concurrency_count = 1
    if concurrency_count > 1 and not all_inference_server and base_model:
        # FIXME: Could use semaphore to manage each LLM concurrency, in case mix of local and remote
        raise ValueError(
            "Concurrency count > 1 will lead to mixup in cache use for local LLMs, disable this raise at own risk.")

    api_open = bool(int(os.getenv('API_OPEN', str(int(api_open)))))
    allow_api = bool(int(os.getenv('ALLOW_API', str(int(allow_api)))))

    if openai_server and not allow_api:
        print("Cannot enable OpenAI server when allow_api=False")
        openai_server = False
    if agent_server and not allow_api:
        print("Cannot enable Agent server when allow_api=False")
        agent_server = False

    if not os.getenv('CLEAR_CLEAR_TORCH'):
        if clear_torch_cache_level == 0:
            os.environ['CLEAR_CLEAR_TORCH'] = '0'
        elif clear_torch_cache_level == 1:
            os.environ['CLEAR_CLEAR_TORCH'] = '1'

    n_gpus1 = torch.cuda.device_count() if torch.cuda.is_available() else 0
    n_gpus1, gpu_ids = cuda_vis_check(n_gpus1)
    if n_gpus is None:
        n_gpus = n_gpus1

    if load_half is None and t5_type(base_model):
        load_half = False
        print("load_half=%s auto-set for %s to avoid bad generation" % (load_half, base_model), flush=True)

    if n_gpus == 0 or get_device(n_gpus=n_gpus) == "mps":
        # No CUDA GPUs usable

        if get_device(n_gpus=n_gpus) != "mps":
            print("No GPUs detected", flush=True)

        enable_captions = False
        gpu_id = None
        load_8bit = False
        load_4bit = False
        low_bit_mode = 1
        if load_half is None:
            # wouldn't work if specified True, but respect
            load_half = False
        use_flash_attention_2 = False
        load_gptq = ''
        load_awq = ''
        load_exllama = False
        use_gpu_id = False
        if get_device(n_gpus=n_gpus) == "cuda":
            torch.backends.cudnn.benchmark = True
            torch.backends.cudnn.enabled = False
            torch.set_default_dtype(torch.float32)
        if is_public and not inference_server and not model_lock:
            # 12B uses ~94GB
            # 6.9B uses ~47GB
            base_model = 'h2oai/h2ogpt-oig-oasst1-512-6_9b' if not base_model else base_model
        if hf_embedding_model is None:
            # if no GPUs, use simpler embedding model to avoid cost in time
            hf_embedding_model = "sentence-transformers/all-MiniLM-L6-v2"
        if score_model == 'auto':
            score_model = ''
    else:
        if not have_flash_attention_2:
            use_flash_attention_2 = False
        if load_half is None:
            load_half = True
        # CUDA GPUs visible
        if score_model == 'auto':
            if n_gpus >= 2:
                # will by default place scoring model on last GPU
                # avoid score model for now, not really useful
                # score_model = 'OpenAssistant/reward-model-deberta-v3-large-v2'
                score_model = ''
            else:
                score_model = ''
        if hf_embedding_model is None:
            # if still None, then set default
            hf_embedding_model = 'BAAI/bge-large-en-v1.5'

    # get defaults
    if base_model:
        model_lower = base_model.lower()
        model_lower0 = base_model0.lower()
    elif model_lock:
        assert len(model_lock) > 0 and model_lock[0]['base_model'], "model_lock: %s" % model_lock
        # set to '' so don't contaminate other models in lock with first one
        model_lower = ''
        model_lower0 = ''
    else:
        model_lower = ''
        model_lower0 = ''
    if not (gradio or function):
        # force, else not single response like want to look at
        stream_output = False
        # else prompt removal can mess up output
        chat = False
    if not stream_output:
        stream_map = False
    # hard-coded defaults
    first_para = False
    text_limit = None

    if offload_folder:
        offload_folder = makedirs(offload_folder, exist_ok=True, tmp_ok=True, use_base=True)

    # auto-set stt and tts.
    # Done early here for lg_to_gr() and preload of db to know what's enabled
    if cli or not (gradio or function):
        enable_stt = enable_tts = False

    if not (have_soundfile and have_librosa and have_wavio):
        if enable_stt == 'auto':
            print("soundfile, librosa, and wavio not installed, disabling STT", flush=True)
            enable_stt = False
        elif enable_stt is True:
            raise RuntimeError("STT packages (soundfile, librosa, wavio) not installed")
    elif enable_stt == 'auto':
        enable_stt = False
    if n_gpus != 0 and enable_stt:
        print("STT enabled, may use more GPU, set --enable_stt=False for low-memory systems", flush=True)

    if not (have_soundfile and have_librosa and have_wavio):
        if enable_tts == 'auto':
            print("soundfile, librosa, and wavio not installed, disabling TTS", flush=True)
            enable_tts = False
        elif enable_tts is True:
            raise RuntimeError("TTS packages (soundfile, librosa, wavio) not installed")
    elif enable_tts == 'auto':
        enable_tts = False
    if not have_langchain and enable_transcriptions:
        print("Must install langchain for transcription, disabling", flush=True)
        enable_transcriptions = False
    if not (have_soundfile and have_librosa and have_wavio) and enable_tts:
        enable_tts = False
        print("soundfile, librosa, and wavio not installed, disabling TTS", flush=True)
    if n_gpus != 0 and enable_tts:
        print("TTS enabled, may use more GPU, set --enable_tts=False for low-memory systems", flush=True)
    if n_gpus == 0:
        tts_gpu = False
        stt_gpu = False
        caption_gpu = False
        asr_gpu = False

    if n_gpus == 0 and get_device(n_gpus=n_gpus) != "mps":
        # if local DocTR, doesn't work on CPU
        enable_doctr = False
        enable_pdf_doctr = False

    if is_public:
        stt_model = 'distil-whisper/distil-large-v3'

    # defaults
    caption_loader = None
    doctr_loader = None
    pix2struct_loader = None
    asr_loader = None

    image_audio_loaders_options0, image_audio_loaders_options, \
        pdf_loaders_options0, pdf_loaders_options, \
        url_loaders_options0, url_loaders_options = lg_to_gr(**locals().copy())
    jq_schema0 = jq_schema
    extract_frames0 = extract_frames
    guided_whitespace_pattern0 = guided_whitespace_pattern
    metadata_in_context0 = metadata_in_context
    # transcribe
    image_audio_loaders = image_audio_loaders_options0
    pdf_loaders = pdf_loaders_options0
    url_loaders = url_loaders_options0

    placeholder_instruction, placeholder_input, \
        stream_output, show_examples, \
        prompt_type, prompt_dict, chat_template, \
        temperature, top_p, top_k, penalty_alpha, num_beams, \
        max_new_tokens, min_new_tokens, early_stopping, max_time, \
        repetition_penalty, num_return_sequences, \
        do_sample, \
        seed, \
        src_lang, tgt_lang, \
        examples, \
        task_info = \
        get_generate_params(model_lower,
                            model_lower0,
                            inference_server,
                            llamacpp_dict,
                            chat,
                            stream_output, enable_caching, show_examples,
                            prompt_type, prompt_dict, chat_template,
                            system_prompt,
                            pre_prompt_query, prompt_query,
                            pre_prompt_summary, prompt_summary, hyde_llm_prompt,
                            all_docs_start_prompt,
                            all_docs_finish_prompt,

                            user_prompt_for_fake_system_prompt,
                            json_object_prompt,
                            json_object_prompt_simpler,
                            json_code_prompt,
                            json_code_prompt_if_no_schema,
                            json_schema_instruction,
                            json_preserve_system_prompt,
                            json_object_post_prompt_reminder,
                            json_code_post_prompt_reminder,
                            json_code2_post_prompt_reminder,

                            temperature, top_p, top_k, penalty_alpha, num_beams,
                            max_new_tokens, min_new_tokens, early_stopping, max_time,
                            repetition_penalty, num_return_sequences,
                            do_sample,
                            seed,
                            top_k_docs,
                            chunk,
                            chunk_size,
                            image_audio_loaders,
                            pdf_loaders,
                            url_loaders,
                            jq_schema,
                            extract_frames,
                            llava_prompt,
                            docs_ordering_type,
                            min_max_new_tokens,
                            max_input_tokens,
                            max_total_input_tokens,
                            docs_token_handling,
                            docs_joiner,
                            hyde_level,
                            hyde_template,
                            hyde_show_only_final,
                            doc_json_mode,
                            metadata_in_context,
                            chatbot_role,
                            speaker,
                            tts_language,
                            tts_speed,
                            image_file,
                            image_control,
                            images_num_max,
                            image_resolution,
                            image_format,
                            rotate_align_resize_image,
                            video_frame_period,
                            image_batch_image_prompt,
                            image_batch_final_prompt,
                            image_batch_stream,
                            visible_vision_models,
                            video_file,

                            response_format,
                            guided_json,
                            guided_regex,
                            guided_choice,
                            guided_grammar,
                            guided_whitespace_pattern,
                            client_metadata,

                            verbose,
                            )

    git_hash = get_githash()
    locals_dict = locals().copy()
    locals_print = '\n'.join(['%s: %s' % (k, v) for k, v in locals_dict.items()])
    if verbose:
        print(f"Generating model with params:\n{locals_print}", flush=True)
        print("Command: %s\nHash: %s" % (str(' '.join(sys.argv)), git_hash), flush=True)

    # PRELOAD

    if enable_captions:
        if pre_load_image_audio_models:
            from image_captions import H2OImageCaptionLoader
            caption_loader = H2OImageCaptionLoader(caption_gpu=caption_gpu, gpu_id=caption_gpu_id).load_model()
        else:
            caption_loader = 'gpu' if n_gpus > 0 and caption_gpu else 'cpu'
    else:
        caption_loader = False

    if not have_langchain and pre_load_embedding_model:
        print("Must install langchain for preloading embedding model, disabling", flush=True)
        pre_load_embedding_model = False

    if use_openai_embedding:
        # makes later code simpler
        hf_embedding_model = ''

    if pre_load_embedding_model and \
            langchain_mode != LangChainMode.DISABLED.value and \
            not use_openai_embedding:
        from gpt_langchain import get_embedding
        hf_embedding_model = dict(name=hf_embedding_model,
                                  model=get_embedding(use_openai_embedding, hf_embedding_model=hf_embedding_model,
                                                      preload=True, gpu_id=embedding_gpu_id))

    if not (have_doctr and have_langchain) and enable_doctr:
        print("Must install DocTR and LangChain installed if enabled DocTR, disabling", flush=True)
        enable_doctr = False
        enable_pdf_ocr = 'off'

    if enable_doctr or enable_pdf_ocr in [True, 'auto', 'on']:
        if pre_load_image_audio_models:
            from image_doctr import H2OOCRLoader
            doctr_loader = H2OOCRLoader(layout_aware=True, gpu_id=doctr_gpu_id).load_model()
        else:
            doctr_loader = 'gpu' if n_gpus > 0 and caption_gpu else 'cpu'
    else:
        doctr_loader = False

    if enable_transcriptions:
        if pre_load_image_audio_models:
            from audio_langchain import H2OAudioCaptionLoader
            asr_loader = H2OAudioCaptionLoader(asr_gpu=asr_gpu,
                                               gpu_id=asr_gpu_id,
                                               asr_model=asr_model,
                                               use_better=asr_use_better,
                                               use_faster=asr_use_faster).load_model()
        else:
            asr_loader = 'gpu' if n_gpus > 0 and asr_gpu else 'cpu'
    else:
        asr_loader = False

    if enable_stt:
        from stt import transcribe
        if pre_load_image_audio_models and \
                stt_model == asr_model:
            transcriber = asr_loader.model.pipe
        else:
            from stt import get_transcriber
            transcriber = get_transcriber(model=stt_model,
                                          use_gpu=stt_gpu,
                                          gpu_id=stt_gpu_id)
        transcriber_func = functools.partial(transcribe,
                                             transcriber=transcriber,
                                             debug=debug,
                                             max_chunks=30 if is_public else None,
                                             sst_floor=sst_floor,
                                             )

    model_xtt, supported_languages_xtt = None, None
    predict_from_text_func = None
    generate_speech_func = None
    return_as_byte = True  # outside conditional since used without other checks
    if enable_tts:
        # NOTE: required bytes for now for audio streaming to work, else untested combine_audios()
        if tts_model.startswith('microsoft'):
            from tts import predict_from_text, get_tts_model, generate_speech
            processor_tts, model_tts, vocoder_tts = \
                get_tts_model(t5_model=tts_model,
                              t5_gan_model=tts_gan_model,
                              use_gpu=tts_gpu,
                              gpu_id=tts_gpu_id,
                              )
            predict_from_text_func = functools.partial(predict_from_text,
                                                       processor=processor_tts,
                                                       model=model_tts,
                                                       return_as_byte=return_as_byte,
                                                       vocoder=vocoder_tts,
                                                       verbose=verbose)
            generate_speech_func = functools.partial(generate_speech,
                                                     processor=processor_tts,
                                                     model=model_tts,
                                                     vocoder=vocoder_tts,
                                                     return_as_byte=return_as_byte,
                                                     verbose=verbose)
        elif tts_model.startswith('tts_models/'):
            if not have_TTS:
                raise ImportError("Selected non-default Coqui models, but did not install TTS")
            if not have_deepspeed and tts_coquiai_deepspeed:
                tts_coquiai_deepspeed = False
                print("deepspeed not installed, disabling", flush=True)
            from tts_coqui import get_xtt, predict_from_text, generate_speech
            model_xtt, supported_languages_xtt = get_xtt(model_name=tts_model,
                                                         deepspeed=tts_coquiai_deepspeed,
                                                         use_gpu=tts_gpu,
                                                         gpu_id=tts_gpu_id,
                                                         )
            predict_from_text_func = functools.partial(predict_from_text,
                                                       model=model_xtt,
                                                       supported_languages=supported_languages_xtt,
                                                       return_as_byte=return_as_byte,
                                                       verbose=verbose,
                                                       )

            generate_speech_func = functools.partial(generate_speech,
                                                     model=model_xtt,
                                                     supported_languages=supported_languages_xtt,
                                                     return_as_byte=return_as_byte,
                                                     verbose=verbose)

    # setup image models
    from vision.utils_vision import get_image_model_dict
    image_model_dict = get_image_model_dict(enable_image, visible_image_models, image_gpu_ids)
    visible_image_models_state0 = list(image_model_dict.keys())

    # DB SETUP

    if langchain_mode != LangChainMode.DISABLED.value:
        # SECOND PLACE where LangChain referenced, but all imports are kept local so not required
        from gpt_langchain import prep_langchain, get_some_dbs_from_hf, get_persist_directory
        if is_hf:
            get_some_dbs_from_hf()
        dbs = {}
        for langchain_mode1 in langchain_modes:
            if langchain_mode1 in langchain_modes_intrinsic:
                # don't store intrinsic dbs in dbs if db, and don't worry about LLM/Disabled
                continue
            langchain_type = langchain_mode_types.get(langchain_mode1, LangChainTypes.EITHER.value)
            if langchain_type == LangChainTypes.PERSONAL.value:
                # shouldn't prepare per-user databases here
                continue
            persist_directory1, langchain_type = get_persist_directory(langchain_mode1, langchain_type=langchain_type)
            langchain_mode_types[langchain_mode1] = langchain_type
            if langchain_type == LangChainTypes.PERSONAL.value:
                # shouldn't prepare per-user databases here
                continue
            try:
                db = prep_langchain(persist_directory1,
                                    load_db_if_exists,
                                    db_type, use_openai_embedding,
                                    langchain_mode1, langchain_mode_paths, langchain_mode_types,
                                    hf_embedding_model,
                                    migrate_embedding_model,
                                    n_jobs=n_jobs,
                                    embedding_gpu_id=embedding_gpu_id,
                                    kwargs_make_db=locals().copy(),
                                    verbose=verbose)
            finally:
                # in case updated embeddings or created new embeddings
                clear_torch_cache(allow_skip=True)
            dbs[langchain_mode1] = db
        # remove None db's so can just rely upon k in dbs for if hav db
        dbs = {k: v for k, v in dbs.items() if v is not None}
    else:
        dbs = {}
        # import control
        if os.environ.get("TEST_LANGCHAIN_IMPORT"):
            assert 'gpt_langchain' not in sys.modules, "Dev bug, import of langchain when should not have"
            assert 'langchain' not in sys.modules, "Dev bug, import of langchain when should not have"

    # MODEL SETUP

    if attention_sinks:
        if use_cache is False:
            raise ValueError("attention sinks requires use_cache=True")
        else:
            use_cache = True
    # never truncate if using attention sinks
    truncation_generation = truncation_generation and not attention_sinks

    other_model_state_defaults = dict(load_8bit=load_8bit, load_4bit=load_4bit, low_bit_mode=low_bit_mode,
                                      load_half=load_half, use_flash_attention_2=use_flash_attention_2,
                                      load_gptq=load_gptq, load_awq=load_awq, load_exllama=load_exllama,
                                      use_safetensors=use_safetensors,
                                      revision=revision, use_gpu_id=use_gpu_id, gpu_id=gpu_id,
                                      compile_model=compile_model,
                                      use_cache=use_cache,
                                      llamacpp_dict=llamacpp_dict,
                                      rope_scaling=rope_scaling,
                                      max_seq_len=max_seq_len,
                                      max_output_seq_len=max_output_seq_len,
                                      exllama_dict=exllama_dict,
                                      gptq_dict=gptq_dict,
                                      attention_sinks=attention_sinks,
                                      sink_dict=sink_dict,
                                      truncation_generation=truncation_generation,
                                      hf_model_dict=hf_model_dict,
                                      force_seq2seq_type=force_seq2seq_type,
                                      force_t5_type=force_t5_type,
                                      trust_remote_code=trust_remote_code,
                                      )
    assert list(other_model_state_defaults.keys()) == list(other_model_state_defaults0.keys())
    model_state_none = model_state_none0.copy()
    model_state_none.update(other_model_state_defaults)
    # for allowing rest of eval_func_param_names
    for k in eval_func_param_names:
        if k not in model_state_none:
            model_state_none[k] = None

    selection_docs_state0 = dict(langchain_modes=langchain_modes,
                                 langchain_mode_paths=langchain_mode_paths,
                                 langchain_mode_types=langchain_mode_types)
    selection_docs_state = copy.deepcopy(selection_docs_state0)

    if cli or not (gradio or function):
        # initial state for query prompt
        model_name = base_model
        pre_prompt_query, prompt_query, pre_prompt_summary, prompt_summary, hyde_llm_prompt = \
            get_langchain_prompts(pre_prompt_query, prompt_query,
                                  pre_prompt_summary, prompt_summary, hyde_llm_prompt,
                                  )

    # get score model
    score_model_state0 = dict(model=None, tokenizer=None, device=None,
                              base_model=None, display_name=None, tokenizer_base_model='', lora_weights='',
                              inference_server='', prompt_type='', prompt_dict='', chat_template=None,
                              visible_models=None, h2ogpt_key=None,
                              reward_model=None)
    if score_model:
        all_kwargs = locals().copy()
        smodel, stokenizer, sdevice = get_score_model(reward_type=True,
                                                      **get_kwargs(get_score_model, exclude_names=['reward_type'],
                                                                   **all_kwargs))
        score_model_state0.update(dict(model=smodel, tokenizer=stokenizer, device=sdevice,
                                       base_model=score_model,
                                       reward_model=True))

    # get verifier model, replaces score_model if exists
    if verifier_model:
        score_model = verifier_model
        all_kwargs = locals().copy()
        all_kwargs.update(base_model=verifier_model,
                          tokenizer_base_model=verifier_tokenizer_base_model,
                          inference_server=verifier_inference_server,
                          prompt_type=noop_prompt_type, prompt_dict={},
                          chat_template=None,
                          visible_models=None, h2ogpt_key=None)
        smodel, stokenizer, sdevice = get_model_retry(reward_type=False,
                                                      **get_kwargs(get_model, exclude_names=['reward_type'],
                                                                   **all_kwargs))
        score_model_state0.update(dict(model=smodel, tokenizer=stokenizer, device=sdevice,
                                       base_model=verifier_model,
                                       tokenizer_base_model=verifier_tokenizer_base_model,
                                       inference_server=verifier_inference_server,
                                       prompt_type=noop_prompt_type,
                                       reward_model=False))

    # get default model(s)
    model_states = []
    model_state_base0 = {}
    model_state_base0.update(model_state_none)
    model_state_base0.update(dict(base_model=base_model, base_model0=base_model0,
                                  tokenizer_base_model=tokenizer_base_model, lora_weights=lora_weights,
                                  inference_server=inference_server,
                                  prompt_type=prompt_type, prompt_dict=prompt_dict, chat_template=chat_template,
                                  display_name=display_name))
    model_state_base0.update(other_model_state_defaults)
    # for allowing rest of eval_func_param_names.  We don't want to force CLI values always by default
    for k in eval_func_param_names:
        if k not in model_state_base0:
            model_state_base0[k] = None

    model_list = [model_state_base0]
    model_list0 = copy.deepcopy(model_list)  # just strings, safe to deepcopy
    model_state0 = copy.deepcopy(model_state_none)
    assert len(model_state_none) == len(model_state0)
    have_model_lock = model_lock is not None and len(model_lock) > 0
    if have_model_lock:
        model_list = copy.deepcopy(model_lock)

    kwargs_model_lock_to_state = locals().copy()
    kwargs_model_lock_to_state = {k: v for k, v in kwargs_model_lock_to_state.items() if
                                  isinstance(v, (str, dict, int, float, bool, type(None), list))}
    excluded_kwargs_model_lock_to_state_keys = [k for k in locals() if k not in kwargs_model_lock_to_state]
    if verbose:
        print('excluded_kwargs_model_lock_to_state_keys', excluded_kwargs_model_lock_to_state_keys)

    # do reverse, so first is default base_model etc., so some logic works in go_gradio() more easily
    for model_dict in reversed(model_list):
        model_dict.update({k: v for k, v in model_state_none.items() if k not in model_dict})
        # use non-cache since accumulate model_lock and may have to dedup
        model_state_trial = model_lock_to_state(model_dict, cache_model_state=False, **kwargs_model_lock_to_state)
        if not model_state_trial:
            continue
        model_state0 = model_state_trial.copy()
        assert len(model_state_none) == len(model_state0)

        if have_model_lock:
            # last in iteration will be first
            model_states.insert(0, model_state_trial)
            # fill model_state0 so go_gradio() easier, manage model_states separately
            model_state0 = model_state_trial.copy()
        else:
            model_state0 = model_state_trial.copy()

    # begin prompt adjustments
    # get query prompt for (say) last base model if using model lock
    pre_prompt_query1, prompt_query1, pre_prompt_summary1, prompt_summary1, hyde_llm_prompt1 = (
        get_langchain_prompts(pre_prompt_query,
                              prompt_query,
                              pre_prompt_summary,
                              prompt_summary,
                              hyde_llm_prompt,
                              ))
    # if mixed setup, choose non-empty so best models best
    # FIXME: Make per model dict passed through to evaluate
    pre_prompt_query = pre_prompt_query if pre_prompt_query is not None else pre_prompt_query1
    prompt_query = prompt_query if prompt_query is not None else prompt_query1
    pre_prompt_summary = pre_prompt_summary if pre_prompt_summary is not None else pre_prompt_summary1
    prompt_summary = prompt_summary if prompt_summary is not None else prompt_summary1
    hyde_llm_prompt = hyde_llm_prompt if hyde_llm_prompt is not None else hyde_llm_prompt1

    if all_docs_start_prompt == 'auto' or all_docs_finish_prompt == 'auto':
        all_docs_start_prompt = None
        all_docs_finish_prompt = None

    user_prompt_for_fake_system_prompt = user_prompt_for_fake_system_prompt or user_prompt_for_fake_system_prompt0
    json_object_prompt = json_object_prompt or json_object_prompt0
    json_object_prompt_simpler = json_object_prompt_simpler or json_object_prompt_simpler0
    json_code_prompt = json_code_prompt or json_code_prompt0
    json_code_prompt_if_no_schema = json_code_prompt_if_no_schema or json_code_prompt_if_no_schema0
    json_schema_instruction = json_schema_instruction or json_schema_instruction0
    json_object_post_prompt_reminder = json_object_post_prompt_reminder or json_object_post_prompt_reminder0
    json_code_post_prompt_reminder = json_code_post_prompt_reminder or json_code_post_prompt_reminder0
    json_code2_post_prompt_reminder = json_code2_post_prompt_reminder or json_code2_post_prompt_reminder0

    image_batch_image_prompt = image_batch_image_prompt or image_batch_image_prompt0
    image_batch_final_prompt = image_batch_final_prompt or image_batch_final_prompt0
    # end prompt adjustments

    # get initial display name. Use user display name if set
    all_possible_display_names = [
        x.get('base_model', xi) if x.get('base_model', '') != 'llama' or
                                   not x.get('llamacpp_dict').get('model_path_llama', '')
        else x.get('llamacpp_dict').get('model_path_llama', '')
        for xi, x in enumerate(model_states)]
    [x.update(
        dict(display_name=x.get('display_name', all_possible_display_names[xi]) or all_possible_display_names[xi])) for
        xi, x in enumerate(model_states)]
    # dedup display names
    all_possible_display_names = [x['display_name'] for x in model_states]
    display_names = deduplicate_names([x for x in all_possible_display_names])
    all_possible_display_names = display_names
    # save display names
    [x.update(dict(display_name=display_names[xi])) for xi, x in enumerate(model_states)]
    visible_models_state0 = [x for xi, x in enumerate(all_possible_display_names) if
                             visible_models is None or
                             x in visible_models or
                             xi in visible_models]

    # get list of visible vision models
    is_vision_models = [x.get('display_name') for x in model_states if x.get('is_vision_model')]
    all_possible_vision_display_names = [x for x in all_possible_display_names if
                                         is_vision_model(x) or x in is_vision_models]
    vision_display_names = deduplicate_names([x for x in all_possible_vision_display_names])
    all_possible_vision_display_names = vision_display_names
    visible_vision_models_state0 = [x for xi, x in enumerate(all_possible_vision_display_names) if
                                    visible_vision_models is None or
                                    x in visible_vision_models or
                                    xi in visible_vision_models]
    if visible_vision_models_state0:
        # only single choice
        visible_vision_models_state0 = visible_vision_models_state0[0]
    else:
        visible_vision_models_state0 = ''

    # update to be consistent with what is passed from CLI and model chose
    # do after go over all models if multi-model, so don't contaminate
    # This is just so UI shows reasonable correct value, not 2048 dummy value
    if len(model_states) >= 1:
        max_seq_len = model_states[0]['tokenizer'].model_max_length
    elif model_state0 is not None and \
            'tokenizer' in model_state0 and \
            hasattr(model_state0['tokenizer'], 'model_max_length'):
        max_seq_len = model_state0['tokenizer'].model_max_length

    local_kwargs = locals().copy()
    local_kwargs['my_db_state0'] = my_db_state0

    # run
    if cli:
        from cli import run_cli
        return run_cli(**get_kwargs(run_cli, **local_kwargs))
    elif eval:
        from eval import run_eval
        return run_eval(**get_kwargs(run_eval, **local_kwargs))
    elif gradio or prepare_offline_level > 0:
        # imported here so don't require gradio to run generate
        from gradio_runner import go_gradio
        # assume gradio needs everything
        go_gradio(**local_kwargs)
    elif function:
        return local_kwargs


def evaluate_fake(*args, **kwargs):
    if kwargs.get('langchain_action', LangChainAction.QUERY.value) == LangChainAction.EXTRACT.value:
        response = [invalid_key_msg]
    else:
        response = invalid_key_msg
    yield dict(response=response, sources=[],
               save_dict=dict(prompt='INVALID', extra_dict=dict(num_prompt_tokens=0, base_model='')),
               llm_answers=dict(response_raw=response), response_no_refs=response,
               sources_str='', audio=None, prompt_raw='INVALID', error=invalid_key_msg)
    return


# keep in sync with H2oGPTParams
def evaluate(
        model_state,
        my_db_state,
        selection_docs_state,
        requests_state,
        roles_state,

        # START NOTE: Examples must have same order of parameters
        instruction,
        iinput,
        context,
        stream_output,
        enable_caching,
        prompt_type,
        prompt_dict,
        chat_template,
        temperature,
        top_p,
        top_k,
        penalty_alpha,
        num_beams,
        max_new_tokens,
        min_new_tokens,
        early_stopping,
        max_time,
        repetition_penalty,
        num_return_sequences,
        do_sample,
        seed,

        chat,
        instruction_nochat,
        iinput_nochat,
        langchain_mode,
        add_chat_history_to_context,
        langchain_action,
        langchain_agents,
        top_k_docs,
        chunk,
        chunk_size,
        document_subset,
        document_choice,
        document_source_substrings,
        document_source_substrings_op,
        document_content_substrings,
        document_content_substrings_op,

        pre_prompt_query,
        prompt_query,
        pre_prompt_summary,
        prompt_summary,
        hyde_llm_prompt,
        all_docs_start_prompt,
        all_docs_finish_prompt,

        user_prompt_for_fake_system_prompt,
        json_object_prompt,
        json_object_prompt_simpler,
        json_code_prompt,
        json_code_prompt_if_no_schema,
        json_schema_instruction,
        json_preserve_system_prompt,
        json_object_post_prompt_reminder,
        json_code_post_prompt_reminder,
        json_code2_post_prompt_reminder,

        system_prompt,

        image_audio_loaders,
        pdf_loaders,
        url_loaders,
        jq_schema,
        extract_frames,
        llava_prompt,
        visible_models,
        visible_image_models,
        image_size,
        image_quality,
        image_guidance_scale,
        image_num_inference_steps,
        h2ogpt_key,
        add_search_to_context,

        chat_conversation,
        text_context_list,
        docs_ordering_type,
        min_max_new_tokens,
        max_input_tokens,
        max_total_input_tokens,
        docs_token_handling,
        docs_joiner,
        hyde_level,
        hyde_template,
        hyde_show_only_final,
        doc_json_mode,
        metadata_in_context,

        chatbot_role,
        speaker,
        tts_language,
        tts_speed,

        image_file,
        image_control,
        images_num_max,
        image_resolution,
        image_format,
        rotate_align_resize_image,
        video_frame_period,
        image_batch_image_prompt,
        image_batch_final_prompt,
        image_batch_stream,
        visible_vision_models,
        video_file,

        response_format,
        guided_json,
        guided_regex,
        guided_choice,
        guided_grammar,
        guided_whitespace_pattern,

        model_lock,  # not really used by evaluate, just pure API
        client_metadata,

        # END NOTE: Examples must have same order of parameters
        captions_model=None,
        caption_loader=None,
        doctr_loader=None,
        pix2struct_loader=None,
        llava_model=None,
        image_model_dict=None,

        asr_model=None,
        asr_loader=None,

        async_output=None,
        num_async=None,
        src_lang=None,
        tgt_lang=None,
        debug=False,
        concurrency_count=None,
        save_dir=None,
        sanitize_bot_response=False,
        model_state0=None,
        use_auth_token=None,
        trust_remote_code=None,
        memory_restriction_level=None,
        max_max_new_tokens=None,
        is_public=None,
        from_ui=True,
        regenerate_clients=None,
        regenerate_gradio_clients=None,
        validate_clients=None,
        fail_if_invalid_client=None,
        max_max_time=None,
        raise_generate_gpu_exceptions=None,
        lora_weights=None,
        use_llm_if_no_docs=True,
        load_db_if_exists=True,
        dbs=None,
        detect_user_path_changes_every_query=None,
        use_openai_embedding=None,
        use_openai_model=None,
        hf_embedding_model=None,
        migrate_embedding_model=None,
        cut_distance=None,
        db_type=None,
        n_jobs=None,
        first_para=None,
        text_limit=None,
        sources_show_text_in_accordion=None,
        hyde_show_intermediate_in_accordion=None,
        map_reduce_show_intermediate_in_accordion=None,
        top_k_docs_max_show=None,
        show_link_in_sources=None,
        langchain_instruct_mode=None,

        verbose=False,
        gradio=True,
        force_streaming_on_to_handle_timeouts=True,
        cli=False,
        use_cache=None,
        auto_reduce_chunks=None,
        max_chunks=None,
        headsize=None,
        force_langchain_evaluate=None,
        model_state_none=None,
        llamacpp_path=None,
        llamacpp_dict=None,
        exllama_dict=None,
        gptq_dict=None,
        attention_sinks=None,
        sink_dict=None,
        truncation_generation=None,
        hf_model_dict=None,
        force_seq2seq_type=None,
        force_t5_type=None,

        load_exllama=None,
        answer_with_sources=None,
        append_sources_to_answer=None,
        append_sources_to_chat=None,
        image_audio_loaders_options0=None,
        pdf_loaders_options0=None,
        url_loaders_options0=None,
        jq_schema0=None,
        extract_frames0=None,
        guided_whitespace_pattern0=None,
        metadata_in_context0=None,
        keep_sources_in_context=None,
        gradio_errors_to_chatbot=None,
        allow_chat_system_prompt=None,

        # carry defaults to know what forced-off means
        use_pymupdf=None,
        use_unstructured_pdf=None,
        use_pypdf=None,
        enable_pdf_ocr=None,
        enable_pdf_doctr=None,
        try_pdf_as_html=None,

        load_awq=None,

        stream_map=None,
):
    if client_metadata:
        print(f"evaluate start client_metadata: {client_metadata}", flush=True)
    # ensure passed these
    assert concurrency_count is not None
    assert memory_restriction_level is not None
    assert raise_generate_gpu_exceptions is not None
    assert use_openai_embedding is not None
    assert use_openai_model is not None
    assert hf_embedding_model is not None
    assert migrate_embedding_model is not None
    assert db_type is not None
    assert top_k_docs is not None and isinstance(top_k_docs, int)
    assert chunk is not None and isinstance(chunk, bool)
    assert chunk_size is not None and isinstance(chunk_size, int)
    assert n_jobs is not None
    assert first_para is not None
    assert isinstance(add_chat_history_to_context, bool)
    assert isinstance(add_search_to_context, bool)
    assert load_exllama is not None
    # for lazy client (even chat client)
    if image_audio_loaders is None:
        image_audio_loaders = image_audio_loaders_options0
    if pdf_loaders is None:
        pdf_loaders = pdf_loaders_options0
    if url_loaders is None:
        url_loaders = url_loaders_options0
    if jq_schema is None:
        jq_schema = jq_schema0
    if extract_frames is None:
        extract_frames = extract_frames0
    if seed is None:
        seed = 0
    if guided_whitespace_pattern is None:
        if guided_whitespace_pattern0:
            guided_whitespace_pattern = guided_whitespace_pattern0
        if guided_whitespace_pattern == '':
            # translate empty string to None
            guided_whitespace_pattern = None
    if metadata_in_context is None:
        metadata_in_context = metadata_in_context0

    if response_format is None:
        response_format = response_formats[0]
    assert response_format in response_formats, "Invalid response_format: %s, must be in %s" % (
        response_format, response_formats)

    if isinstance(langchain_agents, str):
        if langchain_agents.strip().startswith('['):
            # already list, but as string
            langchain_agents = str_to_list(langchain_agents)
        else:
            # just 1 item and make list
            langchain_agents = [langchain_agents]
    if langchain_agents is None:
        langchain_agents = []

    chat_conversation = str_to_list(chat_conversation)
    text_context_list = str_to_list(text_context_list)
    if not image_size:
        imag_size = image_size_default

    langchain_modes = selection_docs_state['langchain_modes']
    langchain_mode_paths = selection_docs_state['langchain_mode_paths']
    langchain_mode_types = selection_docs_state['langchain_mode_types']

    if debug:
        locals_dict = locals().copy()
        locals_dict.pop('model_state', None)
        locals_dict.pop('model_state0', None)
        locals_dict.pop('model_states', None)
        print(locals_dict)

    if langchain_action in LangChainAction.IMAGE_GENERATE.value:
        t_generate = time.time()
        if isinstance(visible_image_models, list):
            assert len(visible_image_models) > 0, "visible_image_models is empty"
            visible_image_models = visible_image_models[0]
        if visible_image_models == '' and image_model_dict:
            # choose first if nothing passed
            visible_image_models = list(image_model_dict.keys())[0]
        image_model_dict = image_model_dict[visible_image_models]
        pipe, make_image = image_model_dict['pipe'], image_model_dict['make_image']

        filename_image = sanitize_filename("image_%s_%s.png" % (instruction, str(uuid.uuid4())),
                                           file_length_limit=50)
        gradio_tmp = get_gradio_tmp()
        image_file_gen = make_image(instruction,
                                    filename=os.path.join(gradio_tmp, filename_image),
                                    pipe=pipe,
                                    image_size=image_size,
                                    image_quality=image_quality,
                                    image_guidance_scale=float(image_guidance_scale),
                                    image_num_inference_steps=int(image_num_inference_steps),
                                    )
        response = (image_file_gen,)
        # FIXME: Could run this through image model if was selected
        extra_dict = dict(t_generate=time.time() - t_generate,
                          instruction=instruction,
                          prompt_raw=instruction,
                          prompt_type=prompt_type,
                          base_model=LangChainAction.IMAGE_GENERATE.value)
        save_dict = dict(prompt=instruction, output=response, extra_dict=extra_dict)
        yield dict(response=response, sources=[], save_dict=save_dict, llm_answers=dict(response_raw=''),
                   response_no_refs="Generated image for %s" % instruction,
                   sources_str="", prompt_raw=instruction)
        if client_metadata:
            print(f"evaluate finish image client_metadata: {client_metadata}", flush=True)
        return

    no_model_msg = "Please choose a base model with --base_model (CLI) or load in Models Tab (gradio).\n" \
                   "Then start New Conversation"

    if model_state is None:
        model_state = model_state_none.copy()
    if model_state0 is None:
        # e.g. for no gradio case, set dummy value, else should be set
        model_state0 = model_state_none.copy()

    # model_state['model] is only 'model' if should use model_state0
    # model could also be None
    have_fresh_model = model_state['model'] not in [None, 'model', no_model_str]
    have_cli_model = model_state0['model'] not in [None, 'model', no_model_str]

    no_llm_ok = langchain_action in [LangChainAction.IMAGE_GENERATE.value,
                                     LangChainAction.IMAGE_CHANGE.value,
                                     LangChainAction.IMAGE_QUERY.value,
                                     LangChainAction.IMAGE_STYLE.value,
                                     ]

    chosen_model_state = model_state0
    if have_fresh_model:
        # USE FRESH MODEL
        chosen_model_state = model_state
    elif have_cli_model:
        # USE MODEL SETUP AT CLI
        assert isinstance(model_state['model'], (type(None), str))  # expect no fresh model
    elif not no_llm_ok:
        raise AssertionError(no_model_msg)

    # get variables
    model = chosen_model_state['model']
    tokenizer = chosen_model_state['tokenizer']
    device = chosen_model_state['device']
    base_model = chosen_model_state['base_model']
    display_name = chosen_model_state['display_name']
    tokenizer_base_model = chosen_model_state['tokenizer_base_model']
    lora_weights = chosen_model_state['lora_weights']
    inference_server = chosen_model_state['inference_server']
    visible_models = chosen_model_state['visible_models']
    is_vision_model1 = chosen_model_state['is_vision_model']
    is_actually_vision_model1 = chosen_model_state['is_actually_vision_model']
    # use overall key if have, so key for this gradio and any inner gradio
    if chosen_model_state['h2ogpt_key'] is not None:
        h2ogpt_key = chosen_model_state['h2ogpt_key']
    # prefer use input from API over model state
    prompt_type = prompt_type or chosen_model_state['prompt_type']
    prompt_dict = prompt_dict or chosen_model_state['prompt_dict']
    if prompt_type == unknown_prompt_type and chosen_model_state['prompt_type'] not in [None, '', unknown_prompt_type]:
        prompt_type = chosen_model_state['prompt_type']
        prompt_dict = chosen_model_state['prompt_dict']
    # prefer use input from API over model state (see prep_bot())
    images_num_max = images_num_max or chosen_model_state['images_num_max']
    if images_num_max is not None:
        # gradio 3 gr.Number issue
        images_num_max = int(images_num_max)
    if isinstance(image_resolution, str) and image_resolution.strip():
        # from gradio was string of tuple
        image_resolution = ast.literal_eval(image_resolution.strip())
        assert isinstance(image_resolution, (list, tuple))
    image_resolution = image_resolution or chosen_model_state['image_resolution']
    image_format = image_format or chosen_model_state['image_format']
    video_frame_period = video_frame_period or chosen_model_state['video_frame_period']

    if base_model is None and not no_llm_ok:
        raise AssertionError(no_model_msg)

    assert base_model.strip(), no_model_msg
    assert model is not None, "Model is missing"
    assert tokenizer is not None, "Tokenizer is missing"
    model_lower = base_model.lower()
    llamacpp_dict = str_to_dict(llamacpp_dict)

    if chat_template and hasattr(tokenizer, 'apply_chat_template'):
        try:
            tokenizer.chat_template = base64_decode_jinja_template(chat_template)
            messages_test = [dict(role='user', content='Hi'),
                             dict(role='assistant', content='Hello! How can I help you today?')]
            test_prompt = tokenizer.apply_chat_template(messages_test, tokenize=False, add_generation_prompt=True)
            assert isinstance(test_prompt, str)
        except Exception as e:
            print("Could not overwrite %s template: %s" % (base_model, str(e)))
            # can't support
            chat_template = ''
            raise

    # choose chat or non-chat mode
    if not chat:
        if not instruction and instruction_nochat:
            instruction = instruction_nochat
        if not iinput and iinput_nochat:
            iinput = iinput_nochat

    # avoid instruction in chat_conversation itself, since always used as additional context to prompt in what follows
    if isinstance(chat_conversation, list) and \
            len(chat_conversation) > 0 and \
            len(chat_conversation[-1]) == 2 and \
            chat_conversation[-1][0] == instruction and \
            chat_conversation[-1][1] in [None, '']:
        chat_conversation = chat_conversation[:-1]
    if not add_chat_history_to_context:
        # make it easy to ignore without needing add_chat_history_to_context
        # some langchain or unit test may need to then handle more general case
        chat_conversation = []
    # get ready for LLM
    chat_conversation = history_for_llm(chat_conversation)

    # Control generation hyperparameters
    # adjust for bad inputs, e.g. in case also come from API that doesn't get constrained by gradio sliders
    # below is for TGI server, not required for HF transformers
    # limits are chosen similar to gradio_runner.py sliders/numbers
    top_p = min(max(1e-3, top_p), 1.0)
    top_k = min(max(1, int(top_k)), 100)
    penalty_alpha = min(2.0, max(0.0, penalty_alpha))
    if temperature == 0.0 and top_p == 1.0 and top_k == 1:
        do_sample = False
    if temperature > 0.0 or top_p < 1.0 or top_k > 1:
        do_sample = True
    if not do_sample:
        temperature = 0
        top_p = 1.0
        top_k = 1
        seed = 1
    if seed == 0 and do_sample:
        seed = randint(0, 32000)
    # Note: Could do below, but for now gradio way can control do_sample directly
    # elif temperature >= 0.01:
    #     do_sample = True

    max_input_tokens = int(max_input_tokens) if max_input_tokens is not None else -1
    max_total_input_tokens = int(max_total_input_tokens) if max_total_input_tokens is not None else -1
    # FIXME: https://github.com/h2oai/h2ogpt/issues/106
    num_beams = 1 if stream_output else num_beams  # See max_beams in gradio_runner
    if model_lower == 'distilgpt2':
        # always truncate for certain models that totally fail otherwise
        truncation_generation = True
    if not inference_server:
        # can listen to truncation_generation
        pass
    else:
        # these don't support allowing going beyond total context
        truncation_generation = True
    max_max_new_tokens = get_max_max_new_tokens(chosen_model_state,
                                                memory_restriction_level=memory_restriction_level,
                                                max_new_tokens=max_new_tokens,
                                                attention_sinks=attention_sinks,
                                                max_max_new_tokens=max_max_new_tokens,
                                                truncation_generation=truncation_generation)
    if min_max_new_tokens is None:
        # default for nochat api
        min_max_new_tokens = 512
    if max_input_tokens is None:
        max_input_tokens = -1
    if max_total_input_tokens is None:
        max_total_input_tokens = -1
    if docs_ordering_type is None:
        docs_ordering_type = docs_ordering_types_default
    if docs_token_handling is None:
        docs_token_handling = docs_token_handling_default
    if docs_joiner is None:
        docs_joiner = docs_joiner_default
    model_max_length = get_model_max_length(chosen_model_state)
    max_new_tokens = min(max(1, int(max_new_tokens)), max_max_new_tokens)
    min_max_new_tokens = min(min_max_new_tokens, max_new_tokens)
    min_new_tokens = min(max(0, int(min_new_tokens)), max_new_tokens)
    max_time = min(max(0, max_time), max_max_time)
    repetition_penalty = min(max(0.01, repetition_penalty), 3.0)
    num_return_sequences = 1 if chat else min(max(1, int(num_return_sequences)), 10)
    min_top_k_docs, max_top_k_docs, label_top_k_docs = get_minmax_top_k_docs(is_public, from_ui)
    # limit total tokens processed, e.g. for summarization, if public instance
    if is_public:
        # control API too for public case
        if from_ui:
            max_input_tokens = max_input_tokens_public
        else:
            max_input_tokens = max_input_tokens_public_api

        if from_ui:
            max_total_input_tokens = min(max_total_input_tokens, max_total_input_tokens_public)
        else:
            max_total_input_tokens = min(max_total_input_tokens, max_total_input_tokens_public_api)
    top_k_docs = min(max(min_top_k_docs, int(top_k_docs)), max_top_k_docs)
    chunk_size = min(max(128, int(chunk_size)), 2048)
    if not context:
        context = ''

    # NOTE!!!!!!!!!!  Choice of developer.  But only possible to force stream if num_beams=1
    # stream if can, so can control task iteration and time of iteration
    # not required, but helpful for max_time control etc.
    stream_output0 = stream_output
    if force_streaming_on_to_handle_timeouts:
        stream_output = gradio and num_beams == 1

    # https://platform.openai.com/docs/guides/reasoning/beta-limitations
    if base_model in ['o1-mini', 'o1-preview'] and os.getenv('O1STREAM', '0') == '0':
        stream_output = False

    from gradio_utils.grclient import GradioClient
    from gradio_client import Client
    gradio_server = inference_server.startswith('http') and (
            isinstance(model, GradioClient) or isinstance(model, Client))
    h2ogpt_gradio_server = gradio_server and not is_gradio_vision_model(base_model)

    if image_file and hasattr(tokenizer, 'chat_template') and isinstance(tokenizer.chat_template,
                                                                         str) and tokenizer.chat_template:
        if 'Prompting with images is incompatible with system messages' in tokenizer.chat_template:
            system_prompt_xml = f"""\n<system_prompt>\n{system_prompt}\n</system_prompt>\n""" if system_prompt else ''
            if instruction and system_prompt_xml:
                if '<system_prompt>' not in instruction:
                    instruction = system_prompt_xml + '\n\n' + instruction
            else:
                if system_prompt_xml:
                    if '<system_prompt>' not in prompt_query:
                        prompt_query = system_prompt_xml + prompt_query
                    if '<system_prompt>' not in prompt_summary:
                        prompt_summary = system_prompt_xml + prompt_summary
            system_prompt = ''

    if guided_json == '':
        guided_json = None
    if guided_regex == '':
        guided_regex = None
    if guided_grammar == '':
        guided_grammar = None
    if isinstance(guided_choice, str) and guided_choice:
        guided_choice = ast.literal_eval(guided_choice)
        assert isinstance(guided_choice, list), "Wrong type: guided_choice: %s" % guided_choice

    # don't repeat prompting if doing gradio server since inner prompting will handle
    json_vllm = chosen_model_state['json_vllm']  # for guided_choice etc. needs to be outside below conditional block
    json_schema_type = None
    if not h2ogpt_gradio_server and \
            response_format in ['json_object', 'json_code']:

        json_object_prompt = json_object_prompt or json_object_prompt0
        json_object_prompt = '\n' + json_object_prompt + '\n\n'
        json_object_prompt_simpler = json_object_prompt_simpler or json_object_prompt_simpler0
        json_object_prompt_simpler = '\n' + json_object_prompt_simpler + '\n\n'
        json_code_prompt = json_code_prompt or json_code_prompt0
        json_code_prompt = '\n' + json_code_prompt + '\n\n'
        json_code_prompt_if_no_schema = json_code_prompt_if_no_schema or json_code_prompt_if_no_schema0
        json_code_prompt_if_no_schema = '\n' + json_code_prompt_if_no_schema + '\n\n'
        json_schema_instruction = json_schema_instruction or json_schema_instruction0
        json_schema_instruction = '\n' + json_schema_instruction + '\n\n'
        json_object_post_prompt_reminder = json_object_post_prompt_reminder or json_object_post_prompt_reminder0
        json_code_post_prompt_reminder = json_code_post_prompt_reminder or json_code_post_prompt_reminder0
        json_code2_post_prompt_reminder = json_code2_post_prompt_reminder or json_code2_post_prompt_reminder0

        if isinstance(guided_json, str):
            try:
                guided_json = guided_json_properties = json.loads(guided_json)
            except (json.decoder.JSONDecodeError, TypeError):
                try:
                    guided_json = guided_json_properties = ast.literal_eval(guided_json)
                except:
                    guided_json = guided_json_properties = {}
        else:
            guided_json = guided_json_properties = guided_json or {}
        assert isinstance(guided_json_properties, dict), "guided_json_properties must be dict by now"
        if 'properties' in guided_json_properties:
            guided_json_properties = guided_json_properties['properties']
        # back to string, so e.g. do not get ' in prompt but " for quotes etc.  gemma messes that up.
        guided_json_properties_json = json.dumps(guided_json_properties)
        if guided_json_properties_json.startswith('{'):
            json_schema_type = 'object'
        elif guided_json_properties_json.startswith('['):
            json_schema_type = 'array'
        elif guided_json_properties_json.startswith('"'):
            json_schema_type = 'string'
        elif guided_json_properties_json.startswith('true') or guided_json_properties_json.startswith('false'):
            json_schema_type = 'boolean'
        elif guided_json_properties_json.startswith('null'):
            json_schema_type = 'null'
        elif guided_json_properties_json.isdigit():
            json_schema_type = 'number'

        schema_instruction = json_schema_instruction.format(properties_schema=guided_json_properties_json)

        pre_instruction = ''
        post_instruction = ''
        supports_schema = get_supports_schema(inference_server, base_model, response_format, guided_json=guided_json,
                                              json_vllm=json_vllm)

        if supports_schema:
            # for vLLM or claude-3, support schema if given
            # can't give schema both in prompt and tool/guided_json, messes model up
            if json_vllm:
                # e.g. for llama2-13b https://github.com/vllm-project/vllm/issues/4093
                pre_instruction = schema_instruction
        elif is_json_model(base_model, inference_server, json_vllm=json_vllm) and \
                response_format == 'json_object' and \
                not (json_vllm and not guided_json):
            # these models don't support schema if given
            if inference_server and inference_server.startswith('mistral'):
                # mistral-large gets confused with extra info, and not required
                # updates, things changed, revise again
                # https://docs.mistral.ai/capabilities/json_mode/
                json_object_prompt = json_object_prompt_simpler
            # shouldn't have to tell to use json, but should tell schema
            if guided_json_properties:
                # FIXME: Do function calling if can instead
                pre_instruction = json_object_prompt + schema_instruction
            else:
                # OpenAI requires "json" to appear somewhere in messages
                pre_instruction = json_object_prompt
            # often models need reminder to do it in actual JSON
            post_instruction = json_object_post_prompt_reminder
        else:
            # json_code way
            # have to tell to use json and give schema if present
            if guided_json_properties:
                pre_instruction = json_code_prompt + schema_instruction
                post_instruction = json_code_post_prompt_reminder
            else:
                pre_instruction = json_code_prompt + json_code_prompt_if_no_schema
                post_instruction = json_code2_post_prompt_reminder
        # ignore these, make no sense for JSON mode
        if not json_preserve_system_prompt:
            system_prompt = ''  # can mess up the model, e.g. 70b
        if pre_instruction:
            if True or base_model and base_model in anthropic_mapping:
                # NOTE: enabled generally for now, seems to help generally
                pre_instruction = '\n<response_format_instructions>\n' + \
                                  pre_instruction + \
                                  '\n</response_format_instructions>\n\n'
            else:
                pre_instruction = 'Begin response format instructions:\n###\n' + \
                                  pre_instruction + \
                                  '\n###\nEnd response format instructions\n\n'
        if instruction:
            # avoid duplication, assuming instruction will be in final prompt after prompt_query or prompt_summary
            if pre_instruction:
                instruction = pre_instruction + '\n\n' + instruction
            if post_instruction:
                instruction = instruction + '\n\n' + post_instruction
            pre_prompt_query = ''
            pre_prompt_summary = ''
        else:
            pre_prompt_query = ''
            pre_prompt_summary = ''
            if pre_instruction:
                prompt_query = pre_instruction + prompt_query
                prompt_summary = pre_instruction + prompt_summary
            if post_instruction:
                # '' allowed, but don't add extra \n\n if such
                prompt_query = prompt_query + '\n\n' + post_instruction if prompt_query else post_instruction
                prompt_summary = prompt_summary + '\n\n' + post_instruction if prompt_summary else post_instruction

    ###############
    # prompt_type and prompter setup
    if inference_server.startswith('openai_chat') or inference_server.startswith('openai_azure_chat'):
        # no extra LLM prompting
        prompt_type = 'openai_chat'
    elif inference_server.startswith('vllm_chat'):
        # no extra LLM prompting
        prompt_type = unknown_prompt_type

    # in some cases, like lean nochat API, don't want to force sending prompt_type, allow default choice
    # This doesn't do switch-a-roo, assume already done, so might be wrong model and can't infer
    if prompt_type in ['', None, unknown_prompt_type] and prompt_type != 'custom':
        prompt_type_trial = model_name_to_prompt_type(base_model, inference_server,
                                                      llamacpp_dict=llamacpp_dict, tokenizer=tokenizer)
        if prompt_type_trial:
            prompt_type = prompt_type_trial
            if verbose:
                print("Auto-selecting prompt_type=%s for %s" % (prompt_type, base_model), flush=True)
    assert prompt_type is not None, "prompt_type was None"

    # get prompter
    prompter = Prompter(prompt_type, prompt_dict, debug=debug, stream_output=stream_output,
                        system_prompt=system_prompt, tokenizer=tokenizer, base_model=base_model)

    # THIRD PLACE where LangChain referenced, but imports only occur if enabled and have db to use
    assert langchain_mode in langchain_modes, "Invalid langchain_mode %s not in %s" % (langchain_mode, langchain_modes)
    assert langchain_action in langchain_actions, "Invalid langchain_action %s not in %s" % (
        langchain_action, langchain_actions)
    assert len(
        set(langchain_agents).difference(langchain_agents_list)) == 0, "Invalid langchain_agents %s" % langchain_agents

    # get db, but also fill db state so return already has my_db_state and dbs filled so faster next query
    if langchain_mode != LangChainMode.DISABLED.value:
        from gpt_langchain import get_any_db
        db = get_any_db(my_db_state, langchain_mode, langchain_mode_paths, langchain_mode_types,
                        dbs=dbs,
                        load_db_if_exists=load_db_if_exists,
                        db_type=db_type,
                        use_openai_embedding=use_openai_embedding,
                        hf_embedding_model=hf_embedding_model,
                        migrate_embedding_model=migrate_embedding_model,
                        for_sources_list=True,
                        verbose=verbose,
                        n_jobs=n_jobs,
                        )
    else:
        db = None

    t_generate = time.time()
    langchain_only_model = base_model in non_hf_types or \
                           load_exllama or \
                           inference_server.startswith('replicate') or \
                           inference_server.startswith('sagemaker') or \
                           inference_server.startswith('openai_azure_chat') or \
                           inference_server.startswith('openai_azure') or \
                           inference_server.startswith('anthropic') or \
                           inference_server.startswith('google') or \
                           inference_server.startswith('mistralai') or \
                           inference_server.startswith('groq') or \
                           (image_file or image_control) and (not gradio_server)
    do_langchain_path = langchain_mode not in [False, 'Disabled', 'LLM'] or \
                        langchain_only_model or \
                        force_langchain_evaluate or \
                        len(text_context_list) > 0

    if len(langchain_agents) > 0:
        do_langchain_path = True
    if add_search_to_context:
        # easier to manage prompt etc. by doing full langchain path
        do_langchain_path = True

    gen_hyper_dict = dict(do_sample=do_sample,
                          seed=seed,
                          temperature=temperature,
                          repetition_penalty=repetition_penalty,
                          top_p=top_p,
                          top_k=top_k,
                          penalty_alpha=penalty_alpha,
                          num_beams=num_beams,
                          min_new_tokens=min_new_tokens,
                          max_new_tokens=max_new_tokens,
                          early_stopping=early_stopping,
                          max_time=max_time,
                          num_return_sequences=num_return_sequences,
                          )
    extra_dict = gen_hyper_dict.copy()
    extra_dict.update(dict(base_model=base_model,
                           display_name=display_name,
                           prompt_type=prompt_type,
                           inference_server=inference_server,
                           langchain_mode=langchain_mode,
                           langchain_action=langchain_action,
                           langchain_agents=langchain_agents,
                           document_subset=document_subset,
                           document_choice=document_choice,
                           document_source_substrings=document_source_substrings,
                           document_source_substrings_op=document_source_substrings_op,
                           document_content_substrings=document_content_substrings,
                           document_content_substrings_op=document_content_substrings_op,
                           add_search_to_context=add_search_to_context,
                           instruction=instruction,
                           iinput=iinput,
                           context=context,
                           ntokens=None,
                           tokens_persecond=None,
                           llamacpp_dict=llamacpp_dict,
                           ))
    save_dict = dict(base_model=base_model, display_name=display_name, save_dir=save_dir, extra_dict=extra_dict)

    if do_langchain_path:
        text = ''
        sources = []
        sources_str = ''
        response = ''
        response_raw = ''
        response_no_refs = ''
        prompt_raw = ''
        # use smaller cut_distance for wiki_full since so many matches could be obtained, and often irrelevant unless close
        from gpt_langchain import run_qa_db
        loaders_dict, captions_model, asr_model = gr_to_lg(image_audio_loaders,
                                                           pdf_loaders,
                                                           url_loaders,
                                                           use_pymupdf=use_pymupdf,
                                                           use_unstructured_pdf=use_unstructured_pdf,
                                                           use_pypdf=use_pypdf,
                                                           enable_pdf_ocr=enable_pdf_ocr,
                                                           enable_pdf_doctr=enable_pdf_doctr,
                                                           try_pdf_as_html=try_pdf_as_html,
                                                           captions_model=captions_model,
                                                           asr_model=asr_model,
                                                           )
        loaders_dict.update(dict(captions_model=captions_model,
                                 caption_loader=caption_loader,
                                 doctr_loader=doctr_loader,
                                 pix2struct_loader=pix2struct_loader,
                                 llava_model=llava_model,
                                 asr_model=asr_model,
                                 asr_loader=asr_loader,
                                 jq_schema=jq_schema,
                                 extract_frames=extract_frames,
                                 llava_prompt=llava_prompt,
                                 ))
        data_point = dict(context=context, instruction=instruction, input=iinput)
        # no longer stuff chat history directly into context this early
        prompt_basic = prompter.generate_prompt(data_point, context_from_history=False, image_file=image_file)
        prompt = prompt_basic
        num_prompt_tokens = 0
        ntokens = None
        llm_answers = {}
        for r in run_qa_db(
                inference_server=inference_server,
                regenerate_clients=regenerate_clients,
                regenerate_gradio_clients=regenerate_gradio_clients,
                validate_clients=validate_clients,
                fail_if_invalid_client=fail_if_invalid_client,
                model_name=base_model, model=model, tokenizer=tokenizer,
                langchain_only_model=langchain_only_model,
                load_awq=load_awq,
                async_output=async_output,
                num_async=num_async,
                prompter=prompter,
                use_llm_if_no_docs=use_llm_if_no_docs,
                load_db_if_exists=load_db_if_exists,
                db=db,
                langchain_mode_paths=langchain_mode_paths,
                langchain_mode_types=langchain_mode_types,
                detect_user_path_changes_every_query=detect_user_path_changes_every_query,
                cut_distance=1.1 if langchain_mode in ['wiki_full'] else cut_distance,
                answer_with_sources=answer_with_sources,
                append_sources_to_answer=append_sources_to_answer,
                append_sources_to_chat=append_sources_to_chat,
                add_chat_history_to_context=add_chat_history_to_context,
                add_search_to_context=add_search_to_context,
                keep_sources_in_context=keep_sources_in_context,
                gradio_errors_to_chatbot=gradio_errors_to_chatbot,
                memory_restriction_level=memory_restriction_level,
                system_prompt=system_prompt,
                allow_chat_system_prompt=allow_chat_system_prompt,
                use_openai_embedding=use_openai_embedding,
                use_openai_model=use_openai_model,
                hf_embedding_model=hf_embedding_model,
                migrate_embedding_model=migrate_embedding_model,
                first_para=first_para,
                text_limit=text_limit,
                sources_show_text_in_accordion=sources_show_text_in_accordion,
                hyde_show_intermediate_in_accordion=hyde_show_intermediate_in_accordion,
                map_reduce_show_intermediate_in_accordion=map_reduce_show_intermediate_in_accordion,
                top_k_docs_max_show=top_k_docs_max_show,
                show_link_in_sources=show_link_in_sources,
                langchain_instruct_mode=langchain_instruct_mode,

                # evaluate args items
                query=instruction,
                iinput=iinput,
                context=context,
                stream_output0=stream_output0,
                stream_output=stream_output,
                enable_caching=enable_caching,
                chunk=chunk,
                chunk_size=chunk_size,

                **loaders_dict,

                langchain_mode=langchain_mode,
                langchain_action=langchain_action,
                langchain_agents=langchain_agents,
                document_subset=document_subset,
                document_choice=document_choice,
                document_source_substrings=document_source_substrings,
                document_source_substrings_op=document_source_substrings_op,
                document_content_substrings=document_content_substrings,
                document_content_substrings_op=document_content_substrings_op,
                top_k_docs=top_k_docs,
                prompt_type=prompt_type,
                prompt_dict=prompt_dict,
                chat_template=chat_template,
                pre_prompt_query=pre_prompt_query,
                prompt_query=prompt_query,
                pre_prompt_summary=pre_prompt_summary,
                prompt_summary=prompt_summary,
                hyde_llm_prompt=hyde_llm_prompt,
                all_docs_start_prompt=all_docs_start_prompt,
                all_docs_finish_prompt=all_docs_finish_prompt,

                user_prompt_for_fake_system_prompt=user_prompt_for_fake_system_prompt,
                json_object_prompt=json_object_prompt,
                json_object_prompt_simpler=json_object_prompt_simpler,
                json_code_prompt=json_code_prompt,
                json_code_prompt_if_no_schema=json_code_prompt_if_no_schema,
                json_schema_instruction=json_schema_instruction,
                json_preserve_system_prompt=json_preserve_system_prompt,
                json_object_post_prompt_reminder=json_object_post_prompt_reminder,
                json_code_post_prompt_reminder=json_code_post_prompt_reminder,
                json_code2_post_prompt_reminder=json_code2_post_prompt_reminder,

                text_context_list=text_context_list,
                chat_conversation=chat_conversation,
                visible_models=visible_models,
                h2ogpt_key=h2ogpt_key,
                docs_ordering_type=docs_ordering_type,
                min_max_new_tokens=min_max_new_tokens,
                max_input_tokens=max_input_tokens,
                max_total_input_tokens=max_total_input_tokens,
                docs_token_handling=docs_token_handling,
                docs_joiner=docs_joiner,
                hyde_level=hyde_level,
                hyde_template=hyde_template,
                hyde_show_only_final=hyde_show_only_final,
                doc_json_mode=doc_json_mode,
                metadata_in_context=metadata_in_context,

                **gen_hyper_dict,

                db_type=db_type,
                n_jobs=n_jobs,
                verbose=verbose,
                cli=cli,
                sanitize_bot_response=sanitize_bot_response,

                lora_weights=lora_weights,
                llamacpp_path=llamacpp_path,
                llamacpp_dict=llamacpp_dict,
                exllama_dict=exllama_dict,
                gptq_dict=gptq_dict,
                attention_sinks=attention_sinks,
                sink_dict=sink_dict,
                truncation_generation=truncation_generation,
                hf_model_dict=hf_model_dict,
                force_seq2seq_type=force_seq2seq_type,
                force_t5_type=force_t5_type,

                auto_reduce_chunks=auto_reduce_chunks,
                max_chunks=max_chunks,
                headsize=headsize,

                image_file=image_file,
                image_control=image_control,
                images_num_max=images_num_max,
                image_resolution=image_resolution,
                image_format=image_format,
                rotate_align_resize_image=rotate_align_resize_image,
                video_frame_period=video_frame_period,
                image_batch_image_prompt=image_batch_image_prompt,
                image_batch_final_prompt=image_batch_final_prompt,
                image_batch_stream=image_batch_stream,
                visible_vision_models=visible_vision_models,
                video_file=video_file,

                response_format=response_format,
                guided_json=guided_json,
                guided_regex=guided_regex,
                guided_choice=guided_choice,
                guided_grammar=guided_grammar,
                guided_whitespace_pattern=guided_whitespace_pattern,
                client_metadata=client_metadata,

                json_vllm=json_vllm,

                from_ui=from_ui,
                stream_map=stream_map,

                is_vision_model1=is_vision_model1,
                is_actually_vision_model1=is_actually_vision_model1,
        ):
            # doesn't accumulate, new answer every yield, so only save that full answer
            response = r['response']
            if response_format in ['json_object', 'json_code']:
                response_raw = response
                # this can get expensive if long, so only do if small, else do only at end
                if len(str(response)) < max_stream_string_for_json:
                    response = get_json(response, json_schema_type=json_schema_type)
            sources = r['sources']
            num_prompt_tokens = r['num_prompt_tokens']
            ntokens = r.get('ntokens')
            llm_answers = r['llm_answers']
            llm_answers['response_raw'] = response_raw
            response_no_refs = r['response_no_refs']
            sources_str = r['sources_str']
            prompt_raw = str(r['prompt_raw'])
            if stream_output:
                yield dict(response=response, sources=[], save_dict={}, llm_answers=llm_answers,
                           response_no_refs=response_no_refs, sources_str='', prompt_raw='')
        extra_dict.update(dict(num_prompt_tokens=num_prompt_tokens,
                               t_generate=time.time() - t_generate,
                               # tokens_persecond computed in save_generate_output
                               sources_str=sources_str,
                               sources=sources,
                               ntokens=ntokens,
                               ))
        if response_format in ['json_object', 'json_code']:
            # always do at end, in case didn't before due to length
            response = get_json(response, json_schema_type=json_schema_type)
        save_dict.update(dict(prompt=prompt, output=response, where_from="run_qa_db", extra_dict=extra_dict))
        yield dict(response=response, sources=sources, save_dict=save_dict, llm_answers=llm_answers,
                   response_no_refs=response_no_refs, sources_str=sources_str, prompt_raw=prompt_raw)
        if verbose:
            print(
                'Post-Generate Langchain: %s decoded_output: %s' %
                (str(datetime.now()), len(response) if response else -1),
                flush=True)
        if response or sources or langchain_only_model:
            # if got no response (e.g. not showing sources and got no sources,
            # so nothing to give to LLM), then slip through and ask LLM
            # Or if llama/gptj, then just return since they had no response and can't go down below code path
            # don't clear torch cache here, delays multi-generation, and bot(), all_bot(), and evaluate_nochat() do it
            if client_metadata:
                print(f"evaluate finish run_qa_db client_metadata: {client_metadata}", flush=True)
            return

    if client_metadata:
        print(f"evaluate middle non-langchain client_metadata: {client_metadata}", flush=True)
    # NOT LANGCHAIN PATH, raw LLM
    # restrict instruction + , typically what has large input
    prompt, \
        instruction, iinput, context, \
        num_prompt_tokens, max_new_tokens, num_prompt_tokens0, num_prompt_tokens_actual, \
        history_to_use_final, external_handle_chat_conversation, \
        top_k_docs_trial, one_doc_size, truncation_generation, system_prompt, _, _ = \
        get_limited_prompt(instruction,
                           iinput,
                           tokenizer,
                           prompter=prompter,
                           base_model=base_model,
                           inference_server=inference_server,
                           # prompt_type=prompt_type,  # use prompter
                           # prompt_dict=prompt_dict,  # use prompter
                           # chat=chat,  # use prompter
                           max_new_tokens=max_new_tokens,
                           # system_prompt=system_prompt,  # use prompter
                           allow_chat_system_prompt=allow_chat_system_prompt,
                           context=context,
                           chat_conversation=chat_conversation,
                           user_prompt_for_fake_system_prompt=user_prompt_for_fake_system_prompt,
                           keep_sources_in_context=keep_sources_in_context,
                           model_max_length=model_max_length,
                           memory_restriction_level=memory_restriction_level,
                           langchain_mode=langchain_mode,
                           add_chat_history_to_context=add_chat_history_to_context,
                           min_max_new_tokens=min_max_new_tokens,
                           max_input_tokens=max_input_tokens,
                           max_total_input_tokens=max_total_input_tokens,
                           truncation_generation=truncation_generation,
                           gradio_server=gradio_server,
                           attention_sinks=attention_sinks,
                           hyde_level=hyde_level,
                           gradio_errors_to_chatbot=gradio_errors_to_chatbot,
                           # gradio is pass through, we don't make prompt with images here
                           image_file=image_file if not gradio_server else [],
                           is_actually_vision_model=is_actually_vision_model1,
                           )

    if inference_server.startswith('vllm') or \
            inference_server.startswith('sglang') or \
            inference_server.startswith('openai') or \
            inference_server.startswith('http'):
        text = ''
        gen_server_kwargs = {}
        if inference_server.startswith('vllm') or \
                inference_server.startswith('sglang') or \
                inference_server.startswith('openai'):
            # sglang reaches here only for text mode
            assert not inference_server.startswith('openai_azure_chat'), "Not fo Azure, use langchain path"
            assert not inference_server.startswith('openai_azure'), "Not for Azure, use langchain path"
            if isinstance(model, dict):
                openai_client, openai_async_client, inf_type = model['client'], model['async_client'], model['inf_type']
            else:
                openai_client, openai_async_client, \
                    inf_type, _, _, _, _ = set_openai(inference_server, model_name=base_model)
            where_from = inf_type
            responses = None

            terminate_response = prompter.terminate_response or []
            stop_sequences = list(set(terminate_response + [prompter.PreResponse]))
            stop_sequences = [x for x in stop_sequences if x]
            # OpenAI will complain if ask for too many new tokens, takes it as min in some sense, wrongly so.
            max_new_tokens_openai = min(max_new_tokens, model_max_length - num_prompt_tokens_actual)
            gen_server_kwargs = dict(temperature=temperature if do_sample else 0,
                                     max_tokens=max_new_tokens_openai,
                                     top_p=top_p if do_sample else 1,
                                     frequency_penalty=0,
                                     seed=seed,
                                     n=num_return_sequences,
                                     presence_penalty=(repetition_penalty - 1.0) * 2.0 + 0.0,  # so good default
                                     )
            if base_model in ['o1-mini', 'o1-preview']:
                gen_server_kwargs['max_completion_tokens'] = gen_server_kwargs.pop('max_tokens')
                max_reasoning_tokens = int(os.getenv("MAX_REASONING_TOKENS", 25000))
                gen_server_kwargs['max_completion_tokens'] = max_reasoning_tokens + max(100, gen_server_kwargs[
                    'max_completion_tokens'])
                gen_server_kwargs['temperature'] = 1.0
                gen_server_kwargs.pop('presence_penalty', None)
                gen_server_kwargs.pop('n', None)
                gen_server_kwargs.pop('frequency_penalty', None)
                gen_server_kwargs.pop('top_p', None)
            try:
                if inf_type in ['vllm', 'vllm_chat'] and chosen_model_state['json_vllm']:
                    response_format_real = response_format if not (
                            guided_json or guided_regex or guided_choice or guided_grammar) else 'text'
                    vllm_extra_dict = get_vllm_extra_dict(tokenizer, stop_sequences=stop_sequences,
                                                          response_format=response_format_real,
                                                          guided_json=guided_json,
                                                          guided_regex=guided_regex,
                                                          guided_choice=guided_choice,
                                                          guided_grammar=guided_grammar,
                                                          guided_whitespace_pattern=guided_whitespace_pattern,
                                                          # repetition_penalty=repetition_penalty,  # could pass
                                                          )
                else:
                    vllm_extra_dict = {}
                if inf_type in ['vllm', 'sglang', 'openai']:
                    other_dict = dict(timeout=max_time)
                    responses = openai_client.completions.create(
                        model=base_model,
                        # response_format=dict(type=response_format),  Text Completions API can't handle
                        prompt=prompt,
                        **gen_server_kwargs,
                        stop=stop_sequences,
                        **vllm_extra_dict,
                        stream=stream_output,
                        **other_dict,
                    )
                    text = ''
                    sources = []
                    response = ''
                    response_raw = ''
                    if not stream_output:
                        text = responses.choices[0].text
                        if hasattr(responses, 'usage'):
                            print(f"Usage by {base_model}: {responses.usage}")
                        response = prompter.get_response(prompt + text, prompt=prompt,
                                                         sanitize_bot_response=sanitize_bot_response)
                        if response_format in ['json_object', 'json_code']:
                            response = get_json(response, json_schema_type=json_schema_type)
                    else:
                        collected_events = []
                        tgen0 = time.time()
                        for event in responses:
                            collected_events.append(event)  # save the event response
                            delta = event.choices[0].text if event.choices else None  # extract the text
                            if delta:
                                text += delta  # append the text
                                response = prompter.get_response(prompt + text, prompt=prompt,
                                                                 sanitize_bot_response=sanitize_bot_response)
                                if response_format in ['json_object', 'json_code']:
                                    response_raw = response
                                    if len(str(response)) < max_stream_string_for_json:
                                        response = get_json(response, json_schema_type=json_schema_type)
                                yield dict(response=response, sources=sources, save_dict={},
                                           llm_answers=dict(response_raw=response_raw),
                                           response_no_refs=response, sources_str='', prompt_raw='')
                            if time.time() - tgen0 > max_time:
                                if verbose:
                                    print("Took too long for OpenAI or VLLM: %s" % (time.time() - tgen0), flush=True)
                                break
                            time.sleep(0.005)
                        if response_format in ['json_object', 'json_code']:
                            # always do at end, in case didn't before due to length
                            response = get_json(response, json_schema_type=json_schema_type)
                            yield dict(response=response, sources=sources, save_dict={},
                                       llm_answers=dict(response_raw=response_raw),
                                       response_no_refs=response, sources_str='', prompt_raw='')
                elif inf_type in ['vllm_chat', 'openai_chat']:
                    other_dict = dict(timeout=max_time)
                    if system_prompt in [None, 'None', 'auto']:
                        openai_system_prompt = "You are a helpful assistant."
                    else:
                        openai_system_prompt = system_prompt
                    messages0 = []
                    if openai_system_prompt:
                        if prompter.can_handle_system_prompt:
                            messages0.append({"role": "system", "content": openai_system_prompt})
                        else:
                            messages0.append({"role": "user",
                                              "content": user_prompt_for_fake_system_prompt or \
                                                         user_prompt_for_fake_system_prompt0})
                            messages0.append({"role": "assistant", "content": openai_system_prompt})
                    if chat_conversation and add_chat_history_to_context:
                        assert external_handle_chat_conversation, "Should be handling only externally"
                        # history_to_use_final handles token counting issues
                        for message1 in history_to_use_final:
                            if len(message1) == 2 and (message1[0] is None or message1[1] is None):
                                # then not really part of LLM, internal, so avoid
                                continue
                            if len(message1) == 2:
                                if message1[0]:
                                    messages0.append(
                                        {'role': 'user', 'content': gradio_to_llm(message1[0], bot=False)})
                                if message1[1]:
                                    messages0.append(
                                        {'role': 'assistant', 'content': gradio_to_llm(message1[1], bot=True)})
                    if instruction:
                        messages0.append({'role': 'user', 'content': instruction})

                    if response_format == 'json_object' and inf_type == 'openai_chat':
                        other_dict.update(dict(response_format=dict(type=response_format)))

                    # JSON: https://platform.openai.com/docs/guides/text-generation/json-mode
                    if inf_type == 'vllm_chat':
                        model_name = get_model_name(base_model, openai_client)
                    else:
                        model_name = base_model
                    responses = openai_client.chat.completions.create(
                        model=model_name,
                        messages=messages0,
                        stream=stream_output,
                        **gen_server_kwargs,
                        **vllm_extra_dict,
                        **other_dict,
                    )
                    text = ''
                    sources = []
                    response = ''
                    response_raw = ''
                    if not stream_output:
                        if responses.choices is None and responses.model_extra:
                            raise RuntimeError("OpenAI Chat failed: %s" % responses.model_extra)
                        text = responses.choices[0].message.content
                        response = text
                        if response_format in ['json_object', 'json_code']:
                            response_raw = response
                            if len(str(response)) < max_stream_string_for_json:
                                response = get_json(response, json_schema_type=json_schema_type)
                    else:
                        # NOTE: If some stream failure like wrong model, don't get back response and no failure
                        tgen0 = time.time()
                        for chunk in responses:
                            delta = chunk.choices[0].delta.content if chunk.choices else None
                            if delta:
                                text += delta
                                response = text
                                if response_format in ['json_object', 'json_code']:
                                    response_raw = response
                                    response = get_json(response, json_schema_type=json_schema_type)
                                yield dict(response=response, sources=sources, save_dict={},
                                           llm_answers=dict(response_raw=response_raw),
                                           response_no_refs=response, sources_str='', prompt_raw='')
                            if time.time() - tgen0 > max_time:
                                if verbose:
                                    print("Took too long for OpenAI or VLLM Chat: %s" % (time.time() - tgen0),
                                          flush=True)
                                break
                        if response_format in ['json_object', 'json_code']:
                            # always do at end, in case didn't before due to length
                            response = get_json(response, json_schema_type=json_schema_type)
                            yield dict(response=response, sources=sources, save_dict={},
                                       llm_answers=dict(response_raw=response_raw),
                                       response_no_refs=response, sources_str='', prompt_raw='')
                else:
                    raise RuntimeError("No such OpenAI mode: %s" % inference_server)
            finally:
                if responses is not None:
                    try:
                        responses.close()
                    except Exception as e:
                        print("Failed to close OpenAI response: %s" % str(e), flush=True)
                if regenerate_clients and openai_client is not None:
                    try:
                        openai_client.close()
                    except Exception as e:
                        print("Failed to close OpenAI client: %s" % str(e), flush=True)

        elif inference_server.startswith('http'):
            sources = []
            inference_server0 = inference_server
            inference_server, _, _, _ = get_hf_server(inference_server)
            from text_generation import Client as HFClient
            if isinstance(model, GradioClient) and not regenerate_gradio_clients:
                gr_client = model.clone()
                hf_client = None
            elif isinstance(model, Client) and not regenerate_gradio_clients:
                gr_client = model
                hf_client = None
            elif isinstance(model, HFClient) and not regenerate_gradio_clients:
                gr_client = None
                hf_client = model
            else:
                inference_server, gr_client, hf_client = get_client_from_inference_server(inference_server0,
                                                                                          base_model=base_model,
                                                                                          validate_clients=validate_clients,
                                                                                          fail_if_invalid_client=fail_if_invalid_client,
                                                                                          verbose=verbose)
            llava_direct_gradio = gr_client is not None and '/textbox_api_submit' in [x.api_name for x in
                                                                                      gr_client.endpoints]

            if is_gradio_vision_model(base_model) and llava_direct_gradio:
                where_from = "gr_client for llava"

                # NOTE: llava doesn't handle context or system prompt directly
                from image_utils import get_image_file
                # comes out as list
                img_file = get_image_file(image_file, image_control, document_choice, base_model=base_model,
                                          images_num_max=images_num_max, image_resolution=image_resolution,
                                          image_format=image_format)
                # if images_num_max is None
                img_file = img_file[:llava_num_max]
                num_prompt_tokens += 1500 * len(img_file)  # estimate for single image
                llava_kwargs = dict(file=img_file,
                                    llava_model=inference_server,
                                    # prompt=instruction,
                                    prompt=prompt,  # prepared prompt with chat history etc.
                                    chat_conversation=chat_conversation,
                                    allow_prompt_auto=False,
                                    image_model=base_model,
                                    temperature=temperature,
                                    top_p=top_p,
                                    max_new_tokens=max_new_tokens,
                                    min_max_new_tokens=min_max_new_tokens,
                                    tokenizer=tokenizer,
                                    client=gr_client if not regenerate_gradio_clients else None,
                                    verbose=verbose,
                                    )
                response = ''
                response_raw = ''
                if not stream_output and img_file == 1:
                    from vision.utils_vision import get_llava_response
                    response, _ = get_llava_response(**llava_kwargs)

                    if response_format in ['json_object', 'json_code']:
                        response_raw = response
                        response = get_json(response, json_schema_type=json_schema_type)
                    yield dict(response=response, sources=[], save_dict={}, error='',
                               llm_answers=dict(response_raw=response_raw),
                               response_no_refs=response, sources_str='', prompt_raw='')
                else:
                    tgen0 = time.time()
                    from vision.utils_vision import get_llava_stream
                    for response1 in get_llava_stream(**llava_kwargs):
                        if response_format in ['json_object', 'json_code']:
                            response_raw = response1
                            if len(str(response)) < max_stream_string_for_json:
                                response = get_json(response1, json_schema_type=json_schema_type)
                        else:
                            response = response1
                        yield dict(response=response, sources=[], save_dict={}, error='',
                                   llm_answers=dict(response_raw=response_raw),
                                   response_no_refs=response, sources_str='', prompt_raw='')

                        if time.time() - tgen0 > max_time:
                            if verbose:
                                print("Took too long for TGI: %s" % (time.time() - tgen0), flush=True)
                            break
                    if response_format in ['json_object', 'json_code']:
                        # always do at end, in case didn't before due to length
                        response = get_json(response, json_schema_type=json_schema_type)
                        yield dict(response=response, sources=sources, save_dict={},
                                   llm_answers=dict(response_raw=response_raw),
                                   response_no_refs=response, sources_str='', prompt_raw='')
            else:
                if gr_client is not None:
                    # Note: h2oGPT gradio server could handle input token size issues for prompt,
                    # but best to handle here so send less data to server

                    chat_client = chat
                    where_from = "gr_client"
                    client_langchain_mode = LangChainMode.LLM.value
                    client_add_chat_history_to_context = add_chat_history_to_context
                    client_add_search_to_context = False
                    client_langchain_action = LangChainAction.QUERY.value
                    client_langchain_agents = []
                    gen_server_kwargs = dict(temperature=temperature,
                                             top_p=top_p,
                                             top_k=top_k,
                                             penalty_alpha=penalty_alpha,
                                             num_beams=num_beams,
                                             max_new_tokens=max_new_tokens,
                                             min_new_tokens=min_new_tokens,
                                             early_stopping=early_stopping,
                                             max_time=max_time,
                                             repetition_penalty=repetition_penalty,
                                             num_return_sequences=num_return_sequences,
                                             do_sample=do_sample,
                                             seed=seed,
                                             chat=chat_client,
                                             )
                    # account for gradio into gradio that handles prompting, avoid duplicating prompter prompt injection
                    if prompt_type in [None, '', PromptType.plain.name, PromptType.plain.value,
                                       str(PromptType.plain.value)]:
                        # if our prompt is plain, assume either correct or gradio server knows different prompt type,
                        # so pass empty prompt_Type
                        gr_prompt_type = ''
                        gr_prompt_dict = ''
                        gr_prompt = prompt  # already prepared prompt
                        gr_context = ''
                        gr_iinput = ''
                        gr_chat_template = None
                    else:
                        # if already have prompt_type that is not plain, None, or '', then already applied some prompting
                        #  But assume server can handle prompting, and need to avoid double-up.
                        #  Also assume server can do better job of using stopping.py to stop early, so avoid local prompting, let server handle
                        #  So avoid "prompt" and let gradio server reconstruct from prompt_type we passed
                        # Note it's ok that prompter.get_response() has prompt+text, prompt=prompt passed,
                        #  because just means extra processing and removal of prompt, but that has no human-bot prompting doesn't matter
                        #  since those won't appear
                        gr_context = context
                        gr_prompt = instruction
                        gr_iinput = iinput
                        gr_prompt_type = prompt_type
                        gr_prompt_dict = prompt_dict
                        gr_chat_template = chat_template

                    # ensure image in correct format
                    from image_utils import get_image_file
                    img_file = get_image_file(image_file, image_control, document_choice,
                                              base_model=base_model, images_num_max=images_num_max,
                                              image_resolution=image_resolution, image_format=image_format,
                                              convert=True)  # comes out as list

                    client_kwargs = dict(instruction=gr_prompt if chat_client else '',  # only for chat=True
                                         iinput=gr_iinput,  # only for chat=True
                                         context=gr_context,
                                         # streaming output is supported, loops over and outputs each generation in streaming mode
                                         # but leave stream_output=False for simple input/output mode
                                         stream_output=stream_output,
                                         enable_caching=enable_caching,

                                         **gen_server_kwargs,

                                         prompt_type=gr_prompt_type,
                                         prompt_dict=gr_prompt_dict,
                                         chat_template=gr_chat_template,

                                         instruction_nochat=gr_prompt if not chat_client else '',
                                         iinput_nochat=gr_iinput,  # only for chat=False
                                         langchain_mode=client_langchain_mode,

                                         add_chat_history_to_context=client_add_chat_history_to_context,
                                         chat_conversation=chat_conversation,
                                         text_context_list=text_context_list,

                                         chatbot_role=chatbot_role,
                                         speaker=speaker,
                                         tts_language=tts_language,
                                         tts_speed=tts_speed,

                                         langchain_action=client_langchain_action,
                                         langchain_agents=client_langchain_agents,
                                         top_k_docs=top_k_docs,
                                         chunk=chunk,
                                         chunk_size=chunk_size,
                                         document_subset=DocumentSubset.Relevant.name,
                                         document_choice=[DocumentChoice.ALL.value],
                                         document_source_substrings=[],
                                         document_source_substrings_op='and',
                                         document_content_substrings=[],
                                         document_content_substrings_op='and',
                                         pre_prompt_query=pre_prompt_query,
                                         prompt_query=prompt_query,
                                         pre_prompt_summary=pre_prompt_summary,
                                         prompt_summary=prompt_summary,
                                         hyde_llm_prompt=hyde_llm_prompt,
                                         all_docs_start_prompt=all_docs_start_prompt,
                                         all_docs_finish_prompt=all_docs_finish_prompt,

                                         user_prompt_for_fake_system_prompt=user_prompt_for_fake_system_prompt,
                                         json_object_prompt=json_object_prompt,
                                         json_object_prompt_simpler=json_object_prompt_simpler,
                                         json_code_prompt=json_code_prompt,
                                         json_code_prompt_if_no_schema=json_code_prompt_if_no_schema,
                                         json_schema_instruction=json_schema_instruction,
                                         json_preserve_system_prompt=json_preserve_system_prompt,
                                         json_object_post_prompt_reminder=json_object_post_prompt_reminder,
                                         json_code_post_prompt_reminder=json_code_post_prompt_reminder,
                                         json_code2_post_prompt_reminder=json_code2_post_prompt_reminder,

                                         system_prompt=system_prompt,
                                         image_audio_loaders=image_audio_loaders,
                                         pdf_loaders=pdf_loaders,
                                         url_loaders=url_loaders,
                                         jq_schema=jq_schema,
                                         extract_frames=extract_frames,
                                         llava_prompt=llava_prompt,
                                         visible_models=visible_models,
                                         visible_image_models=visible_image_models,
                                         image_size=image_size,
                                         image_quality=image_quality,
                                         image_guidance_scale=image_guidance_scale,
                                         image_num_inference_steps=image_num_inference_steps,
                                         h2ogpt_key=h2ogpt_key,
                                         add_search_to_context=client_add_search_to_context,
                                         docs_ordering_type=docs_ordering_type,
                                         min_max_new_tokens=min_max_new_tokens,
                                         max_input_tokens=max_input_tokens,
                                         max_total_input_tokens=max_total_input_tokens,
                                         docs_token_handling=docs_token_handling,
                                         docs_joiner=docs_joiner,
                                         hyde_level=hyde_level,
                                         hyde_template=hyde_template,
                                         hyde_show_only_final=hyde_show_only_final,
                                         doc_json_mode=doc_json_mode,
                                         metadata_in_context=metadata_in_context,

                                         image_file=img_file,
                                         image_control=None,  # already stuffed into image_file
                                         images_num_max=images_num_max,
                                         image_resolution=None,  # already changed
                                         image_format=None,  # already changed
                                         rotate_align_resize_image=None,  # already changed
                                         video_frame_period=None,  # already changed
                                         image_batch_image_prompt=image_batch_image_prompt,
                                         image_batch_final_prompt=image_batch_final_prompt,
                                         image_batch_stream=image_batch_stream,
                                         visible_vision_models=visible_vision_models,
                                         video_file=video_file,

                                         response_format=response_format,
                                         guided_json=guided_json,
                                         guided_regex=guided_regex,
                                         guided_choice=guided_choice,
                                         guided_grammar=guided_grammar,
                                         guided_whitespace_pattern=guided_whitespace_pattern,

                                         model_lock=None,  # already set
                                         client_metadata=client_metadata,
                                         )
                    assert len(set(list(client_kwargs.keys())).symmetric_difference(eval_func_param_names)) == 0
                    api_name = '/submit_nochat_api'  # NOTE: like submit_nochat but stable API for string dict passing
                    response = ''
                    response_raw = ''
                    text = ''
                    sources = []
                    strex = ''
                    if not stream_output:
                        res = gr_client.predict(str(dict(client_kwargs)), api_name=api_name)
                        res_dict = ast.literal_eval(res)
                        GradioClient.check_error(res_dict)
                        text = res_dict['response']
                        sources = res_dict['sources']
                        response = prompter.get_response(prompt + text, prompt=prompt,
                                                         sanitize_bot_response=sanitize_bot_response)
                    else:
                        new_stream = False  # hanging for many chatbots
                        gr_stream_kwargs = dict(client_kwargs=client_kwargs,
                                                api_name=api_name,
                                                prompt=prompt, prompter=prompter,
                                                sanitize_bot_response=sanitize_bot_response,
                                                max_time=max_time,
                                                is_public=is_public,
                                                verbose=verbose)
                        if new_stream:
                            gener = gr_client.stream(**gr_stream_kwargs)
                        else:
                            gener = gr_client.simple_stream(**gr_stream_kwargs)
                        response = ''
                        response_raw = ''
                        res_dict = {}
                        for res_dict1 in gener:
                            if 'response' in res_dict1:
                                response = res_dict1['response']
                                if response_format in ['json_object', 'json_code']:
                                    response_raw = response
                                    if len(str(response)) < max_stream_string_for_json:
                                        response = get_json(response, json_schema_type=json_schema_type)
                                    res_dict1['response'] = response
                                    res_dict1['llm_answers'] = res_dict1.get('llm_answers', {})
                                    res_dict1['llm_answers']['response_raw'] = response_raw
                            res_dict = res_dict1
                            yield res_dict1
                        if response_format in ['json_object', 'json_code']:
                            # always do at end, in case didn't before due to length
                            response = get_json(response, json_schema_type=json_schema_type)
                            res_dict['response'] = response
                            res_dict['llm_answers'] = res_dict.get('llm_answers', {})
                            res_dict['llm_answers']['response_raw'] = response_raw
                            yield res_dict

                    # listen to inner gradio
                    num_prompt_tokens += res_dict.get('save_dict', {}).get('extra_dict', {}).get('num_prompt_tokens',
                                                                                                 num_prompt_tokens)
                    prompt = res_dict.get('prompt_raw', prompt)
                elif hf_client:
                    # quick sanity check to avoid long timeouts, just see if can reach server
                    requests.get(inference_server, timeout=int(os.getenv('REQUEST_TIMEOUT_FAST', '10')))
                    # HF inference server needs control over input tokens
                    where_from = "hf_client"
                    response = ''
                    response_raw = ''
                    sources = []

                    # prompt must include all human-bot like tokens, already added by prompt
                    # https://github.com/huggingface/text-generation-inference/tree/main/clients/python#types
                    terminate_response = prompter.terminate_response or []
                    stop_sequences = list(set(terminate_response + [prompter.PreResponse]))
                    stop_sequences = [x for x in stop_sequences if x]
                    gen_server_kwargs = dict(do_sample=do_sample,
                                             max_new_tokens=max_new_tokens,
                                             # best_of=None,
                                             repetition_penalty=repetition_penalty,
                                             return_full_text=False,
                                             seed=seed,
                                             stop_sequences=stop_sequences,
                                             temperature=max(1e-2, temperature),
                                             top_k=top_k,
                                             top_p=min(max(1e-2, top_p), 1.0 - 1e-3),
                                             # truncate=False,  # behaves oddly
                                             # typical_p=top_p,
                                             # watermark=False,
                                             # decoder_input_details=False,
                                             )
                    # work-around for timeout at constructor time, will be issue if multi-threading,
                    # so just do something reasonable or max_time if larger
                    # lower bound because client is re-used if multi-threading
                    hf_client.timeout = max(300, max_time)
                    if not stream_output:
                        text = hf_client.generate(prompt, **gen_server_kwargs).generated_text
                        response = prompter.get_response(prompt + text, prompt=prompt,
                                                         sanitize_bot_response=sanitize_bot_response)
                        if response_format in ['json_object', 'json_code']:
                            response_raw = response
                            response = get_json(response, json_schema_type=json_schema_type)
                    else:
                        tgen0 = time.time()
                        text = ""
                        for responses in hf_client.generate_stream(prompt, **gen_server_kwargs):
                            if not responses.token.special:
                                # stop_sequences
                                text_chunk = responses.token.text
                                text += text_chunk
                                response = prompter.get_response(prompt + text, prompt=prompt,
                                                                 sanitize_bot_response=sanitize_bot_response)
                                sources = []
                                if response_format in ['json_object', 'json_code']:
                                    response_raw = response
                                    if len(str(response)) < max_stream_string_for_json:
                                        response = get_json(response, json_schema_type=json_schema_type)
                                yield dict(response=response, sources=sources, save_dict={},
                                           llm_answers=dict(response_raw=response_raw),
                                           response_no_refs=response, sources_str='', prompt_raw='')
                                time.sleep(0.005)
                            if time.time() - tgen0 > max_time:
                                if verbose:
                                    print("Took too long for TGI: %s" % (time.time() - tgen0), flush=True)
                                break
                        if response_format in ['json_object', 'json_code']:
                            # always do at end, in case didn't before due to length
                            response = get_json(response, json_schema_type=json_schema_type)
                            yield dict(response=response, sources=sources, save_dict={},
                                       llm_answers=dict(response_raw=response_raw),
                                       response_no_refs=response, sources_str='', prompt_raw='')
                else:
                    raise RuntimeError("Failed to get client: %s" % inference_server)
            if isinstance(model, GradioClient) and not regenerate_gradio_clients and gr_client is not None:
                if gr_client.server_hash != model.server_hash:
                    with filelock.FileLock(os.path.join('locks', 'gradio_client.lock')):
                        model.refresh_client()
        else:
            raise RuntimeError("No such inference_server  %s" % inference_server)

        # only return yield with save_dict and prompt_raw here to keep streaming light
        extra_dict.update(gen_server_kwargs)
        ntokens = extra_dict.get('ntokens', None)
        extra_dict.update(dict(inference_server=inference_server,  # changes in some cases
                               num_prompt_tokens=num_prompt_tokens,
                               t_generate=time.time() - t_generate,
                               ntokens=ntokens,
                               prompt_type=prompt_type,
                               tokens_persecond=None,
                               ))
        save_dict.update(dict(prompt=prompt, output=text, where_from=where_from, extra_dict=extra_dict))
        # if not streaming, only place yield should be done
        yield dict(response=response, sources=sources, save_dict=save_dict, llm_answers=dict(response_raw=response_raw),
                   response_no_refs=response, sources_str='', prompt_raw=prompt)
        if client_metadata:
            print(f"evaluate finish inference server client_metadata: {client_metadata}", flush=True)
        return
    else:
        assert not inference_server, "inference_server=%s not supported" % inference_server

    if isinstance(tokenizer, str):
        # pipeline
        if tokenizer == "summarization":
            key = 'summary_text'
        else:
            raise RuntimeError("No such task type %s" % tokenizer)
        # NOTE: uses max_length only
        sources = []
        response = model(prompt, max_length=max_new_tokens)[0][key]
        response_raw = ''
        yield dict(response=response, sources=sources, save_dict=save_dict,
                   llm_answers=dict(response_raw=response_raw),
                   response_no_refs=response, sources_str='', prompt_raw=prompt)
        return

    if 'mbart-' in base_model.lower():
        assert src_lang is not None
        tokenizer.src_lang = languages_covered()[src_lang]

    stopping_criteria = get_stopping(prompt_type, prompt_dict, tokenizer, device, base_model,
                                     model_max_length=model_max_length,
                                     prompter=prompter,
                                     truncation_generation=truncation_generation)

    inputs = tokenizer(prompt, return_tensors="pt")
    if debug and len(inputs["input_ids"]) > 0:
        print('input_ids length', len(inputs["input_ids"][0]), flush=True)
    input_ids = inputs["input_ids"].to(device)
    # CRITICAL LIMIT else will fail
    max_max_tokens = int(tokenizer.model_max_length)
    max_input_tokens_default = max(0, int(max_max_tokens - min_new_tokens))
    if max_input_tokens >= 0:
        max_input_tokens = min(max_input_tokens_default, max_input_tokens)
    else:
        max_input_tokens = max_input_tokens_default
    # NOTE: Don't limit up front due to max_new_tokens, let go up to max or reach max_max_tokens in stopping.py
    assert isinstance(max_input_tokens, int), "Bad type for max_input_tokens=%s %s" % (
        max_input_tokens, type(max_input_tokens))
    input_ids = input_ids[:, -max_input_tokens:]
    # required for falcon if multiple threads or asyncio accesses to model during generation
    if use_cache is None:
        use_cache = False if 'falcon' in base_model else True
    if attention_sinks:
        assert use_cache, "attention sinks requires use_cache=True"
    bad_word_ids = [tokenizer.eos_token_id]
    gen_config_kwargs = dict(num_beams=num_beams,
                             do_sample=do_sample,
                             seed=seed,
                             repetition_penalty=float(repetition_penalty),
                             num_return_sequences=num_return_sequences,
                             renormalize_logits=True,
                             remove_invalid_values=True,
                             use_cache=use_cache,
                             max_new_tokens=max_new_tokens,  # unsure if required here
                             token=use_auth_token,
                             trust_remote_code=trust_remote_code,
                             )
    if do_sample:
        gen_config_kwargs.update(dict(temperature=float(temperature),
                                      top_p=float(top_p),
                                      top_k=top_k))
    if penalty_alpha > 0:
        gen_config_kwargs.update(dict(penalty_alpha=penalty_alpha))
    if True:
        # unclear impact, some odd things going on inside
        # leads to:
        # The attention mask and the pad token id were not set. As a consequence, you may observe unexpected behavior. Please pass your input's `attention_mask` to obtain reliable results.
        # Setting `pad_token_id` to `eos_token_id`:2 for open-end generation.
        # or leads to:
        # Using cls_token, but it is not set yet.
        # Using mask_token, but it is not set yet.
        # Using pad_token, but it is not set yet.
        # Using sep_token, but it is not set yet.
        token_ids = ['eos_token_id', 'pad_token_id', 'bos_token_id', 'cls_token_id', 'sep_token_id']
        for token_id in token_ids:
            if hasattr(tokenizer, token_id) and getattr(tokenizer, token_id) is not None:
                gen_config_kwargs.update({token_id: getattr(tokenizer, token_id)})
    generation_config = GenerationConfig(**gen_config_kwargs)

    gen_kwargs = dict(input_ids=input_ids,
                      generation_config=generation_config,
                      return_dict_in_generate=True,
                      output_scores=True,
                      max_new_tokens=max_new_tokens,  # prompt + new
                      min_new_tokens=min_new_tokens,  # prompt + new
                      early_stopping=early_stopping,  # False, True, "never"
                      max_time=max_time,
                      stopping_criteria=stopping_criteria,
                      )
    if use_cache and attention_sinks:
        from transformers import SinkCache
        sink_dict['window_length'] = sink_dict.get('window_length', max_input_tokens)
        sink_dict['num_sink_tokens'] = sink_dict.get('num_sink_tokens', 4)
        cache = SinkCache(**sink_dict)
        gen_kwargs.update(dict(past_key_values=cache))
    if 'gpt2' in base_model.lower():
        gen_kwargs.update(dict(bos_token_id=tokenizer.bos_token_id, pad_token_id=tokenizer.eos_token_id))
    elif 'mbart-' in base_model.lower():
        assert tgt_lang is not None
        tgt_lang = languages_covered()[tgt_lang]
        gen_kwargs.update(dict(forced_bos_token_id=tokenizer.lang_code_to_id[tgt_lang]))
    else:
        token_ids = ['eos_token_id', 'bos_token_id', 'pad_token_id']
        for token_id in token_ids:
            if hasattr(tokenizer, token_id) and getattr(tokenizer, token_id) is not None:
                gen_kwargs.update({token_id: getattr(tokenizer, token_id)})

    decoder_kwargs = dict(skip_special_tokens=True,
                          clean_up_tokenization_spaces=True)

    decoder = functools.partial(tokenizer.decode,
                                **decoder_kwargs
                                )
    with torch.no_grad():
        have_lora_weights = lora_weights not in [no_lora_str, '', None]
        context_class_cast = NullContext if device == 'cpu' or have_lora_weights or device == 'mps' else torch.autocast
        if t5_type(base_model):
            # issues when casting to float16, can mess up t5 model, e.g. only when not streaming, or other odd behaviors
            context_class_cast = NullContext
        with context_class_cast(device):
            # protection for gradio not keeping track of closed users,
            # else hit bitsandbytes lack of thread safety:
            # https://github.com/h2oai/h2ogpt/issues/104
            # but only makes sense if concurrency_count == 1
            context_class = NullContext  # if concurrency_count > 1 else filelock.FileLock
            if verbose:
                print('Pre-Generate: %s' % str(datetime.now()), flush=True)
            decoded_output = ''
            response = ''
            response_raw = ''
            with context_class("generate.lock"):
                if verbose:
                    print('Generate: %s' % str(datetime.now()), flush=True)
                always_use_streaming_method = True  # to deal with complex parsing of prompt vs. generation due to odd tokenizing
                if stream_output or always_use_streaming_method:
                    skip_prompt = True  # True means first output excludes prompt
                    streamer = H2OTextIteratorStreamer(tokenizer, skip_prompt=skip_prompt, block=False,
                                                       **decoder_kwargs)
                    gen_kwargs.update(dict(streamer=streamer))
                    target = wrapped_partial(generate_with_exceptions, model.generate,
                                             raise_generate_gpu_exceptions=raise_generate_gpu_exceptions,
                                             **gen_kwargs)
                    bucket = queue.Queue()
                    thread = EThread(target=target, streamer=streamer, bucket=bucket)
                    thread.start()
                    ret = dict(response='', sources='', save_dict=dict(), llm_answers=dict(response_raw=response_raw),
                               response_no_refs='', sources_str='', prompt_raw=prompt)
                    outputs = ""
                    sources = []
                    tgen0 = time.time()
                    try:
                        for new_text in streamer:
                            if bucket.qsize() > 0 or thread.exc:
                                thread.join()
                            outputs += new_text
                            response = prompter.get_response(outputs, prompt=None,
                                                             only_new_text=True,
                                                             sanitize_bot_response=sanitize_bot_response)
                            if response_format in ['json_object', 'json_code']:
                                response_raw = response
                                if len(str(response)) < max_stream_string_for_json:
                                    response = get_json(response, json_schema_type=json_schema_type)
                            ret = dict(response=response, sources=sources, save_dict=save_dict,
                                       llm_answers=dict(response_raw=response_raw),
                                       response_no_refs=response, sources_str='', prompt_raw=prompt)
                            if stream_output:
                                yield ret
                            if time.time() - tgen0 > max_time:
                                if verbose:
                                    print("Took too long for Torch: %s" % (time.time() - tgen0), flush=True)
                                break
                        if response_format in ['json_object', 'json_code']:
                            response = get_json(response, json_schema_type=json_schema_type)
                            ret = dict(response=response, sources=sources, save_dict=save_dict,
                                       llm_answers=dict(response_raw=response_raw),
                                       response_no_refs=response, sources_str='', prompt_raw=prompt)
                        if stream_output:
                            # will yield at end if required
                            # yield if anything left over as can happen (FIXME: Understand better)
                            yield ret
                    except BaseException:
                        # if any exception, raise that exception if was from thread, first
                        if thread.exc:
                            raise thread.exc
                        raise
                    finally:
                        # don't clear torch cache here, delays multi-generation, and bot(), all_bot(), and evaluate_nochat() do it
                        # in case no exception and didn't join with thread yet, then join
                        if not thread.exc:
                            thread.join()
                    # in case raise StopIteration or broke queue loop in streamer, but still have exception
                    if thread.exc:
                        raise thread.exc
                    decoded_output = outputs
                    ntokens = len(outputs) // 4  # hack for now
                else:
                    # below length removal doesn't work in general, because encoding does not match internal of model generation
                    input_ids_len = gen_kwargs['input_ids'][0].shape[0]
                    try:
                        outputs = model.generate(**gen_kwargs)
                    finally:
                        pass
                        # don't clear torch cache here, delays multi-generation, and bot(), all_bot(), and evaluate_nochat() do it
                    # skip first IDs
                    ntokens = sum([len(s) - input_ids_len for s in outputs.sequences]) if save_dir else -1
                    outputs = [decoder(s[input_ids_len:]) for s in outputs.sequences]
                    sources = []
                    response = prompter.get_response(outputs, prompt=None,
                                                     only_new_text=True,
                                                     sanitize_bot_response=sanitize_bot_response)
                    if response_format in ['json_object', 'json_code']:
                        response_raw = response
                        response = get_json(response, json_schema_type=json_schema_type)
                    if outputs and len(outputs) >= 1:
                        decoded_output = prompt + outputs[0]

            # full return with save_dict and prompt_raw
            # if not streaming, only place yield should be
            extra_dict.update(gen_config_kwargs)
            extra_dict.update(dict(num_prompt_tokens=num_prompt_tokens,
                                   t_generate=time.time() - t_generate,
                                   sources_str='',
                                   ntokens=ntokens,
                                   tokens_persecond=ntokens / (time.time() - t_generate),
                                   ))
            save_dict.update(dict(prompt=prompt, output=decoded_output,
                                  where_from="evaluate_%s" % str(stream_output),
                                  extra_dict=extra_dict))
            yield dict(response=response, sources=sources, save_dict=save_dict,
                       llm_answers=dict(response_raw=response_raw),
                       response_no_refs=response, sources_str='', prompt_raw=prompt)
            if torch.cuda.is_available() and device not in ['cpu', 'mps']:
                torch.cuda.empty_cache()
            if hasattr(model, 'memory') and hasattr(model.memory, 'reset'):
                model.memory.reset()
            if verbose:
                print('Post-Generate: %s decoded_output: %s' % (
                    str(datetime.now()), len(decoded_output) if decoded_output else -1), flush=True)
    if client_metadata:
        print(f"evaluate HF finish client_metadata: {client_metadata}", flush=True)


inputs_list_names = list(inspect.signature(evaluate).parameters)
state_names = input_args_list.copy()  # doesn't have to be the same, but state_names must match evaluate() and how filled then
inputs_kwargs_list = [x for x in inputs_list_names if x not in eval_func_param_names + state_names]


def get_cutoffs(memory_restriction_level, for_context=False, model_max_length=2048, min_max_new_tokens=512):
    # help to avoid errors like:
    # RuntimeError: The size of tensor a (2048) must match the size of tensor b (2049) at non-singleton dimension 3
    # RuntimeError: expected scalar type Half but found Float
    # with - 256
    if memory_restriction_level > 0:
        max_length_tokenize = 768 - 256 if memory_restriction_level <= 2 else 512 - 256
    else:
        # at least give room for 1 paragraph output
        max_length_tokenize = model_max_length - min_max_new_tokens
    cutoff_len = max_length_tokenize * 4  # if reaches limit, then can't generate new tokens
    output_smallest = 30 * 4
    max_prompt_length = cutoff_len - output_smallest

    if for_context:
        # then lower even more to avoid later chop, since just estimate tokens in context bot
        max_prompt_length = max(64, int(max_prompt_length * 0.8))

    return cutoff_len, output_smallest, max_length_tokenize, max_prompt_length


class H2OTextIteratorStreamer(TextIteratorStreamer):
    """
    normally, timeout required for now to handle exceptions, else get()
    but with H2O version of TextIteratorStreamer, loop over block to handle
    """

    def __init__(self, tokenizer, skip_prompt: bool = False, timeout: typing.Optional[float] = None,
                 block=True, **decode_kwargs):
        super().__init__(tokenizer, skip_prompt, **decode_kwargs)
        self.text_queue = queue.Queue()
        self.stop_signal = None
        self.do_stop = False
        self.timeout = timeout
        self.block = block

    def on_finalized_text(self, text: str, stream_end: bool = False):
        """Put the new text in the queue. If the stream is ending, also put a stop signal in the queue."""
        self.text_queue.put(text, timeout=self.timeout)
        if stream_end:
            self.text_queue.put(self.stop_signal, timeout=self.timeout)

    def __iter__(self):
        return self

    def __next__(self):
        while True:
            try:
                value = self.stop_signal  # value looks unused in pycharm, not true
                if self.do_stop:
                    print("hit stop", flush=True)
                    # could raise or break, maybe best to raise and make parent see if any exception in thread
                    self.clear_queue()
                    self.do_stop = False
                    raise StopIteration()
                    # break
                value = self.text_queue.get(block=self.block, timeout=self.timeout)
                break
            except queue.Empty:
                time.sleep(0.005)
        if value == self.stop_signal:
            self.clear_queue()
            self.do_stop = False
            raise StopIteration()
        else:
            return value

    def clear_queue(self):
        # make sure streamer is reusable after stop hit
        with self.text_queue.mutex:
            self.text_queue.queue.clear()

    def put(self, value):
        """
        Receives tokens, decodes them, and prints them to stdout as soon as they form entire words.
        # same as base class, except remove hack w.r.t. text.rfind(" ") that ruins LLaMa2
        """
        if len(value.shape) > 1 and value.shape[0] > 1:
            raise ValueError("TextStreamer only supports batch size 1")
        elif len(value.shape) > 1:
            value = value[0]

        if self.skip_prompt and self.next_tokens_are_prompt:
            self.next_tokens_are_prompt = False
            return

        # Add the new token to the cache and decodes the entire thing.
        self.token_cache.extend(value.tolist())
        text = self.tokenizer.decode(self.token_cache, **self.decode_kwargs)

        # After the symbol for a new line, we flush the cache.
        if text.endswith("\n"):
            printable_text = text[self.print_len:]
            self.token_cache = []
            self.print_len = 0
        # If the last token is a CJK character, we print the characters.
        elif len(text) > 0 and self._is_chinese_char(ord(text[-1])):
            printable_text = text[self.print_len:]
            self.print_len += len(printable_text)
        # Otherwise, prints until the last space char (simple heuristic to avoid printing incomplete words,
        # which may change with the subsequent token -- there are probably smarter ways to do this!)
        elif len(text) > 0 and text[-1] == '�':
            printable_text = text[self.print_len: text.rfind(" ") + 1]
            self.print_len += len(printable_text)
        else:
            printable_text = text[self.print_len:]
            self.print_len += len(printable_text)

        self.on_finalized_text(printable_text)


def generate_with_exceptions(func, *args, raise_generate_gpu_exceptions=True, **kwargs):
    try:
        func(*args, **kwargs)
    except torch.cuda.OutOfMemoryError as e:
        print("GPU OOM 2: exception: %s" % str(e),
              flush=True)
        if 'input_ids' in kwargs:
            if kwargs['input_ids'] is not None:
                kwargs['input_ids'].cpu()
            kwargs['input_ids'] = None
        traceback.print_exc()
        clear_torch_cache()
        return
    except (Exception, RuntimeError) as e:
        if 'Expected all tensors to be on the same device' in str(e) or \
                'expected scalar type Half but found Float' in str(e) or \
                'probability tensor contains either' in str(e) or \
                'cublasLt ran into an error!' in str(e) or \
                'mat1 and mat2 shapes cannot be multiplied' in str(e):
            print(
                "GPU Error: exception: %s" % str(e),
                flush=True)
            traceback.print_exc()
            clear_torch_cache()
            if raise_generate_gpu_exceptions:
                raise
            return
        else:
            clear_torch_cache()
            if raise_generate_gpu_exceptions:
                raise


def get_generate_params(model_lower,
                        model_lower0,
                        inference_server,
                        llamacpp_dict,
                        chat,
                        stream_output, enable_caching, show_examples,
                        prompt_type, prompt_dict, chat_template,
                        system_prompt,
                        pre_prompt_query, prompt_query,
                        pre_prompt_summary, prompt_summary, hyde_llm_prompt,
                        all_docs_start_prompt, all_docs_finish_prompt,
                        user_prompt_for_fake_system_prompt,
                        json_object_prompt,
                        json_object_prompt_simpler,
                        json_code_prompt,
                        json_code_prompt_if_no_schema,
                        json_schema_instruction,
                        json_preserve_system_prompt,
                        json_object_post_prompt_reminder,
                        json_code_post_prompt_reminder,
                        json_code2_post_prompt_reminder,
                        temperature, top_p, top_k, penalty_alpha, num_beams,
                        max_new_tokens, min_new_tokens, early_stopping, max_time,
                        repetition_penalty, num_return_sequences,
                        do_sample,
                        seed,
                        top_k_docs, chunk, chunk_size,
                        image_audio_loaders,
                        pdf_loaders,
                        url_loaders,
                        jq_schema,
                        extract_frames,
                        llava_prompt,
                        docs_ordering_type,
                        min_max_new_tokens,
                        max_input_tokens,
                        max_total_input_tokens,
                        docs_token_handling,
                        docs_joiner,
                        hyde_level,
                        hyde_template,
                        hyde_show_only_final,
                        doc_json_mode,
                        metadata_in_context,

                        chatbot_role,
                        speaker,
                        tts_language,
                        tts_speed,

                        image_file,
                        image_control,
                        images_num_max,
                        image_resolution,
                        image_format,
                        rotate_align_resize_image,
                        video_frame_period,
                        image_batch_image_prompt,
                        image_batch_final_prompt,
                        image_batch_stream,
                        visible_vision_models,
                        video_file,

                        response_format,
                        guided_json,
                        guided_regex,
                        guided_choice,
                        guided_grammar,
                        guided_whitespace_pattern,
                        client_metadata,

                        verbose,
                        ):
    use_defaults = False
    use_default_examples = True
    examples = []
    task_info = 'LLM'
    if model_lower:
        print(f"Using Model {model_lower}", flush=True)
    else:
        if verbose:
            print("No model defined yet", flush=True)

    min_new_tokens = min_new_tokens if min_new_tokens is not None else 0
    early_stopping = early_stopping if early_stopping is not None else False
    max_time_defaults = 60 * 10
    max_time = max_time if max_time is not None else max_time_defaults

    if prompt_type in ['', None, unknown_prompt_type] and prompt_type != 'custom':
        prompt_type_trial = model_name_to_prompt_type(model_lower, inference_server,
                                                      model_name0=model_lower0,
                                                      llamacpp_dict=llamacpp_dict)
        if prompt_type_trial:
            prompt_type = prompt_type_trial
            if verbose:
                print("Auto-selecting prompt_type=%s for %s" % (prompt_type, model_lower), flush=True)

    # examples at first don't include chat, instruction_nochat, iinput_nochat, added at end
    if show_examples is None:
        if chat:
            show_examples = False
        else:
            show_examples = False

    summarize_example1 = """Jeff: Can I train a ? Transformers model on Amazon SageMaker?
Philipp: Sure you can use the new Hugging Face Deep Learning Container.
Jeff: ok.
Jeff: and how can I get started?
Jeff: where can I find documentation?
Philipp: ok, ok you can find everything here. https://huggingface.co/blog/the-partnership-amazon-sagemaker-and-hugging-face"""

    use_placeholder_instruction_as_example = False
    if 'bart-large-cnn-samsum' in model_lower or 'flan-t5-base-samsum' in model_lower:
        placeholder_instruction = summarize_example1
        placeholder_input = ""
        use_defaults = True
        use_default_examples = False
        use_placeholder_instruction_as_example = True
        task_info = "Summarization"
    elif 't5-' in model_lower or 't5' == model_lower or 'flan-' in model_lower:
        placeholder_instruction = "The square root of x is the cube root of y. What is y to the power of 2, if x = 4?"
        placeholder_input = ""
        use_defaults = True
        use_default_examples = True
        task_info = "Multi-Task: Q/A, translation, Chain-of-Thought, Logical Reasoning, Summarization, etc.  Best to use task prefix as trained on, e.g. `translate English to German: ` (space after colon)"
    elif 'mbart-' in model_lower:
        placeholder_instruction = "The girl has long hair."
        placeholder_input = ""
        use_defaults = True
        use_default_examples = False
        use_placeholder_instruction_as_example = True
    elif 'gpt2' in model_lower:
        placeholder_instruction = "The sky is"
        placeholder_input = ""
        prompt_type = prompt_type or noop_prompt_type
        use_default_examples = True  # some will be odd "continuations" but can be ok
        use_placeholder_instruction_as_example = True
        task_info = "Auto-complete phrase, code, etc."
        use_defaults = True
    else:
        if chat:
            placeholder_instruction = ""
        else:
            placeholder_instruction = "Give detailed answer for whether Einstein or Newton is smarter."
        placeholder_input = ""
        if prompt_type in ['', None, unknown_prompt_type] and prompt_type != 'custom':
            prompt_type_trial = model_name_to_prompt_type(model_lower,
                                                          inference_server,
                                                          model_name0=model_lower0,
                                                          llamacpp_dict=llamacpp_dict)
            if prompt_type_trial:
                prompt_type = prompt_type_trial
            # default is unknown, because might rely upon trust_remote_code to handle prompting
            if model_lower:
                prompt_type = prompt_type or unknown_prompt_type
        task_info = "No task"
        if prompt_type == 'instruct':
            task_info = "Answer question or follow imperative as instruction with optionally input."
        elif prompt_type in [empty_prompt_type, noop_prompt_type, unknown_prompt_type]:
            task_info = "Auto-complete phrase, code, etc."
        elif prompt_type == 'human_bot':
            if chat:
                task_info = "Chat (Shift-Enter to give question/imperative, input concatenated with instruction)"
            else:
                task_info = "Ask question/imperative (input concatenated with instruction)"

    # revert to plain if still nothing
    if model_lower:
        prompt_type = prompt_type or unknown_prompt_type
    else:
        prompt_type = prompt_type or unknown_prompt_type
    if use_defaults:
        temperature = 0.0 if temperature is None else temperature
        top_p = 1.0 if top_p is None else top_p
        top_k = 1 if top_k is None else top_k
        penalty_alpha = 0 if penalty_alpha is None else penalty_alpha
        num_beams = num_beams or 1
        max_new_tokens = max_new_tokens or 512
        repetition_penalty = repetition_penalty or 1.0  # 1.07 causes issues still with more repetition
        num_return_sequences = min(num_beams, num_return_sequences or 1)
        do_sample = False if do_sample is None else do_sample
    else:
        temperature = 0.0 if temperature is None else temperature
        top_p = 1.0 if top_p is None else top_p
        top_k = 1 if top_k is None else top_k
        penalty_alpha = 0 if penalty_alpha is None else penalty_alpha
        num_beams = num_beams or 1
        max_new_tokens = max_new_tokens or 1024
        repetition_penalty = repetition_penalty or 1.0  # 1.07 causes issues still with more repetition
        num_return_sequences = min(num_beams, num_return_sequences or 1)
        do_sample = False if do_sample is None else do_sample
    # doesn't include chat, instruction_nochat, iinput_nochat, added later
    params_list = ["",
                   stream_output, enable_caching,
                   prompt_type, prompt_dict, chat_template,
                   temperature, top_p, top_k, penalty_alpha, num_beams,
                   max_new_tokens, min_new_tokens,
                   early_stopping, max_time, repetition_penalty, num_return_sequences, do_sample, seed]

    if use_placeholder_instruction_as_example:
        examples += [[placeholder_instruction, ''] + params_list]

    if use_default_examples:
        examples += [
            ["Translate English to French", "Good morning"] + params_list,
            ["Give detailed answer for whether Einstein or Newton is smarter.", ''] + params_list,
            ["Explain in detailed list, all the best practices for coding in python.", ''] + params_list,
            [
                "Create a markdown table with 3 rows for the primary colors, and 2 columns, with color name and hex codes.",
                ''] + params_list,
            ['Translate to German:  My name is Arthur', ''] + params_list,
            ["Please answer to the following question. Who is going to be the next Ballon d'or?", ''] + params_list,
            ['Can Geoffrey Hinton have a conversation with George Washington? Give the rationale before answering.',
             ''] + params_list,
            ['Please answer the following question. What is the boiling point of Nitrogen?', ''] + params_list,
            ['Answer the following yes/no question. Can you write a whole Haiku in a single tweet?', ''] + params_list,
            ["Simplify the following expression: (False or False and True). Explain your answer.", ''] + params_list,
            [
                "Premise: At my age you will probably have learnt one lesson. Hypothesis:  It's not certain how many lessons you'll learn by your thirties. Does the premise entail the hypothesis?",
                ''] + params_list,
            ['The square root of x is the cube root of y. What is y to the power of 2, if x = 4?', ''] + params_list,
            [
                'Answer the following question by reasoning step by step.  The cafeteria had 23 apples. If they used 20 for lunch, and bought 6 more, how many apple do they have?',
                ''] + params_list,
            ["""def area_of_rectangle(a: float, b: float):
    \"\"\"Return the area of the rectangle.\"\"\"""", ''] + params_list,
            ["""# a function in native python:
def mean(a):
    return sum(a)/len(a)

# the same function using numpy:
import numpy as np
def mean(a):""", ''] + params_list,
            ["""X = np.random.randn(100, 100)
y = np.random.randint(0, 1, 100)

# fit random forest classifier with 20 estimators""", ''] + params_list,
        ]
    # add summary example
    examples += [
        [summarize_example1,
         'Summarize' if prompt_type not in [noop_prompt_type, 'instruct_simple'] else ''] + params_list]

    src_lang = "English"
    tgt_lang = "Russian"

    # move to correct position
    for example in examples:
        example += [chat, '', '', LangChainMode.DISABLED.value, True,
                    LangChainAction.QUERY.value, [],
                    top_k_docs, chunk, chunk_size,
                    DocumentSubset.Relevant.name, [],
                    [], 'and', [], 'and',
                    pre_prompt_query, prompt_query,
                    pre_prompt_summary, prompt_summary, hyde_llm_prompt,
                    all_docs_start_prompt, all_docs_finish_prompt,

                    user_prompt_for_fake_system_prompt,
                    json_object_prompt,
                    json_object_prompt_simpler,
                    json_code_prompt,
                    json_code_prompt_if_no_schema,
                    json_schema_instruction,
                    json_preserve_system_prompt,
                    json_object_post_prompt_reminder,
                    json_code_post_prompt_reminder,
                    json_code2_post_prompt_reminder,

                    system_prompt,
                    image_audio_loaders,
                    pdf_loaders,
                    url_loaders,
                    jq_schema,
                    extract_frames,
                    llava_prompt,
                    None,  # visible_models
                    None,  # visible_image_models
                    image_size_default,  # image_size
                    image_quality_choices[0],  # image_quality
                    3.0,  # image_guidance_scale
                    30,  # image_num_inference_steps
                    None,  # h2ogpt_key
                    False,  # add_search_to_context
                    None,  # chat_conversation
                    None,  # text_context_list
                    docs_ordering_type,
                    min_max_new_tokens,
                    max_input_tokens,
                    max_total_input_tokens,
                    docs_token_handling,
                    docs_joiner,
                    hyde_level,
                    hyde_template,
                    hyde_show_only_final,
                    doc_json_mode,
                    metadata_in_context,

                    chatbot_role,
                    speaker,
                    tts_language,
                    tts_speed,
                    image_file,
                    image_control,
                    images_num_max,
                    image_resolution,
                    image_format,
                    rotate_align_resize_image,
                    video_frame_period,
                    image_batch_image_prompt,
                    image_batch_final_prompt,
                    image_batch_stream,
                    visible_vision_models,
                    video_file,

                    response_format,
                    guided_json,
                    guided_regex,
                    guided_choice,
                    guided_grammar,
                    guided_whitespace_pattern,
                    None,  # model_lock, only client, don't need default value
                    client_metadata,
                    ]
        # adjust examples if non-chat mode
        if not chat:
            example[eval_func_param_names.index('instruction_nochat')] = example[
                eval_func_param_names.index('instruction')]
            example[eval_func_param_names.index('instruction')] = ''

            example[eval_func_param_names.index('iinput_nochat')] = example[eval_func_param_names.index('iinput')]
            example[eval_func_param_names.index('iinput')] = ''
        assert len(example) == len(eval_func_param_names), "Wrong example: %s %s" % (
            len(example), len(eval_func_param_names))

    if prompt_type == PromptType.custom.name and not prompt_dict:
        raise ValueError("Unexpected to get non-empty prompt_dict=%s for prompt_type=%s" % (prompt_dict, prompt_type))

    # get prompt_dict from prompt_type, so user can see in UI etc., or for custom do nothing except check format
    if prompt_type:
        prompt_dict, error0 = get_prompt(prompt_type, prompt_dict,
                                         context='', reduced=False, making_context=False, return_dict=True,
                                         system_prompt=system_prompt)
        if error0:
            raise RuntimeError("Prompt wrong: %s" % error0)

    return placeholder_instruction, placeholder_input, \
        stream_output, show_examples, \
        prompt_type, prompt_dict, chat_template, \
        temperature, top_p, top_k, penalty_alpha, num_beams, \
        max_new_tokens, min_new_tokens, early_stopping, max_time, \
        repetition_penalty, num_return_sequences, \
        do_sample, \
        seed, \
        src_lang, tgt_lang, \
        examples, \
        task_info


def languages_covered():
    # https://huggingface.co/facebook/mbart-large-50-many-to-many-mmt#languages-covered
    covered = """Arabic (ar_AR), Czech (cs_CZ), German (de_DE), English (en_XX), Spanish (es_XX), Estonian (et_EE), Finnish (fi_FI), French (fr_XX), Gujarati (gu_IN), Hindi (hi_IN), Italian (it_IT), Japanese (ja_XX), Kazakh (kk_KZ), Korean (ko_KR), Lithuanian (lt_LT), Latvian (lv_LV), Burmese (my_MM), Nepali (ne_NP), Dutch (nl_XX), Romanian (ro_RO), Russian (ru_RU), Sinhala (si_LK), Turkish (tr_TR), Vietnamese (vi_VN), Chinese (zh_CN), Afrikaans (af_ZA), Azerbaijani (az_AZ), Bengali (bn_IN), Persian (fa_IR), Hebrew (he_IL), Croatian (hr_HR), Indonesian (id_ID), Georgian (ka_GE), Khmer (km_KH), Macedonian (mk_MK), Malayalam (ml_IN), Mongolian (mn_MN), Marathi (mr_IN), Polish (pl_PL), Pashto (ps_AF), Portuguese (pt_XX), Swedish (sv_SE), Swahili (sw_KE), Tamil (ta_IN), Telugu (te_IN), Thai (th_TH), Tagalog (tl_XX), Ukrainian (uk_UA), Urdu (ur_PK), Xhosa (xh_ZA), Galician (gl_ES), Slovene (sl_SI)"""
    covered = covered.split(', ')
    covered = {x.split(' ')[0]: x.split(' ')[1].replace(')', '').replace('(', '') for x in covered}
    return covered


def score_qa(smodel, stokenizer, question, answer, memory_restriction_level=0):
    if memory_restriction_level > 0:
        max_length_tokenize = 768 - 256 if memory_restriction_level <= 2 else 512 - 256
    elif hasattr(stokenizer, 'model_max_length'):
        max_length_tokenize = stokenizer.model_max_length
    else:
        # limit to 1024, not worth OOMing on reward score
        max_length_tokenize = 2048 - 1024
    cutoff_len = max_length_tokenize * 4  # restrict deberta related to max for LLM

    question = question[-cutoff_len:]
    answer = answer[-cutoff_len:]

    inputs = stokenizer(question, answer,
                        return_tensors="pt",
                        truncation=True,
                        max_length=max_length_tokenize).to(smodel.device)
    try:
        score = torch.sigmoid(smodel(**inputs.to(smodel.device)).logits[0].float()).cpu().detach().numpy()[0]
    except torch.cuda.OutOfMemoryError as e:
        score = 0.0
        print("GPU OOM 3: question: %s answer: %s exception: %s" % (question, answer, str(e)), flush=True)
        del inputs
        traceback.print_exc()
        clear_torch_cache()
        return 'Response Score: GPU OOM'
    except (Exception, RuntimeError) as e:
        score = 0.0
        if 'Expected all tensors to be on the same device' in str(e) or \
                'expected scalar type Half but found Float' in str(e) or \
                'probability tensor contains either' in str(e) or \
                'cublasLt ran into an error!' in str(e) or \
                'device-side assert triggered' in str(e):
            print("GPU Error: question: %s answer: %s exception: %s" % (question, answer, str(e)),
                  flush=True)
            traceback.print_exc()
            clear_torch_cache()
            return 'Response Score: GPU Error'
        else:
            raise
    os.environ['TOKENIZERS_PARALLELISM'] = 'true'
    return score


def check_locals(**kwargs):
    # ensure everything in evaluate is here
    can_skip_because_locally_generated = no_default_param_names + [
        # get_model:
        'reward_type'
    ]
    missing1 = []
    for k in eval_func_param_names:
        if k in can_skip_because_locally_generated:
            continue
        if k not in kwargs:
            missing1.append(k)
    assert not missing1, "Missing %s" % missing1

    missing2 = []
    for k in inputs_kwargs_list:
        if k in can_skip_because_locally_generated:
            continue
        if k not in kwargs:
            missing2.append(k)
    assert not missing2, "Missing %s" % missing2


def get_model_max_length(model_state):
    if not isinstance(model_state['tokenizer'], (str, type(None))):
        return model_state['tokenizer'].model_max_length
    else:
        return 2048


def get_model_max_length_from_tokenizer(tokenizer):
    if hasattr(tokenizer, 'model_max_length'):
        return int(tokenizer.model_max_length)
    else:
        return 2048


def get_max_max_new_tokens(model_state, **kwargs):
    if not isinstance(model_state['tokenizer'], (str, type(None))) or not kwargs.get('truncation_generation', False):
        if hasattr(model_state['tokenizer'], 'max_output_len'):
            max_max_new_tokens = model_state['tokenizer'].max_output_len
        elif hasattr(model_state['tokenizer'], 'model_max_length'):
            max_max_new_tokens = model_state['tokenizer'].model_max_length
        else:
            # e.g. fast up, no model
            max_max_new_tokens = None
    else:
        max_max_new_tokens = None

    if kwargs['max_max_new_tokens'] is not None and max_max_new_tokens is not None:
        if kwargs.get('truncation_generation', False):
            return min(max_max_new_tokens, kwargs['max_max_new_tokens'])
        else:
            # listen to max_max_new_tokens, ignore model limit
            return max(max_max_new_tokens, kwargs['max_max_new_tokens'])
    elif kwargs['max_max_new_tokens'] is not None:
        return kwargs['max_max_new_tokens']
    elif kwargs['memory_restriction_level'] == 1:
        return 768
    elif kwargs['memory_restriction_level'] == 2:
        return 512
    elif kwargs['memory_restriction_level'] >= 3:
        return 256
    else:
        # FIXME: Need to update after new model loaded, so user can control with slider
        return 2048


def get_minmax_top_k_docs(is_public, from_ui):
    label_top_k_docs = "Number of document chunks (query) or pages/parts (summarize)"
    if is_public:
        min_top_k_docs = 1
        if from_ui:
            max_top_k_docs = max_top_k_docs_public
        else:
            max_top_k_docs = max_top_k_docs_public_api
    else:
        min_top_k_docs = -1
        max_top_k_docs = 1000
        label_top_k_docs = label_top_k_docs + " (-1 = auto fill model context, all pages/docs for summarize)"
    return min_top_k_docs, max_top_k_docs, label_top_k_docs


def remove_refs(text, keep_sources_in_context, langchain_mode, hyde_level, gradio_errors_to_chatbot):
    # md -> back to text, maybe not super important if model trained enough
    if not keep_sources_in_context and \
            langchain_mode != 'Disabled' and \
            text.find(super_source_prefix) >= 0:
        # FIXME: This is relatively slow even for small amount of text, like 0.3s each history item
        import re
        text = re.sub(f'{re.escape(super_source_prefix)}.*?{re.escape(super_source_postfix)}', '', text,
                      flags=re.DOTALL)
        if text.endswith('\n<p>'):
            text = text[:-4]

    # HYDE
    in_generic_chat = gradio_errors_to_chatbot or \
                      (hyde_level is None or hyde_level > 0) and \
                      not keep_sources_in_context and \
                      langchain_mode != 'Disabled'
    if in_generic_chat and text.find(generic_prefix) >= 0:
        # FIXME: This is relatively slow even for small amount of text, like 0.3s each history item
        import re
        text = re.sub(f'{re.escape(generic_prefix)}.*?{re.escape(generic_postfix)}', '', text,
                      flags=re.DOTALL)
        if text.endswith('\n<p>'):
            text = text[:-4]

    return text


def history_to_context(history, langchain_mode=None,
                       add_chat_history_to_context=None,
                       prompt_type=None, prompt_dict=None, model_max_length=None,
                       memory_restriction_level=None, keep_sources_in_context=None,
                       system_prompt=None, chat_conversation=None,
                       hyde_level=None,
                       gradio_errors_to_chatbot=None,
                       min_max_new_tokens=512):
    """
    Consumes all history up to (but not including) the latest history item that is presumed to be an [instruction, None] pair.
    :param history:
    :param langchain_mode:
    :param add_chat_history_to_context:
    :param prompt_type:
    :param prompt_dict:
    :param model_max_length:
    :param memory_restriction_level:
    :param keep_sources_in_context:
    :param system_prompt:
    :param chat_conversation:
    :param min_max_new_tokens:
    :return:
    """
    history = merge_chat_conversation_history(chat_conversation, history)
    len_history = len(history)

    # Ensure output will be unique to models
    _, _, _, max_prompt_length = get_cutoffs(memory_restriction_level,
                                             for_context=True, model_max_length=model_max_length,
                                             min_max_new_tokens=min_max_new_tokens)

    # Account for the system prompt length
    if system_prompt:
        system_prompt_length = len(system_prompt)
        max_prompt_length -= system_prompt_length

    context1 = ''
    final_history = []

    if max_prompt_length is not None and add_chat_history_to_context:
        # Compute terminate_response, chat_sep, chat_turn_sep once
        _, pre_response, terminate_response, chat_sep, chat_turn_sep = \
            generate_prompt({}, prompt_type, prompt_dict,
                            reduced=True,
                            making_context=True,
                            system_prompt=system_prompt,
                            histi=-1)

        for histi in range(len_history - 1, -1, -1):  # Iterate in reverse order
            user = history[histi][0]
            bot = history[histi][1]

            if user is None:
                # Used to indicate was error or something similar put into chatbot stream
                continue

            instruction = gradio_to_llm(user, bot=False)
            output = gradio_to_llm(bot, bot=True) if bot is not None else ''

            data_point = dict(instruction=instruction, input='', output=output)
            prompt, _, _, _, _ = \
                generate_prompt(data_point,
                                prompt_type,
                                prompt_dict,
                                reduced=True,
                                making_context=True,
                                system_prompt=system_prompt,
                                histi=histi)
            prompt = remove_refs(prompt, keep_sources_in_context, langchain_mode, hyde_level, gradio_errors_to_chatbot)
            prompt = prompt.replace('<br>', chat_turn_sep)
            if not prompt.endswith(chat_turn_sep):
                prompt += chat_turn_sep

            if len(prompt + context1) > max_prompt_length:
                remaining_length = max_prompt_length - len(context1)
                if len(instruction) > len(output):
                    if len(output) >= remaining_length:
                        truncated_instruction = ''
                        truncated_output = output[:remaining_length]
                    else:
                        truncated_output = output
                        truncated_instruction = instruction[:remaining_length - len(output)]
                else:
                    if len(instruction) >= remaining_length:
                        truncated_instruction = instruction[:remaining_length]
                        truncated_output = ''
                    else:
                        truncated_instruction = instruction
                        truncated_output = output[:remaining_length - len(instruction)]

                data_point = dict(instruction=truncated_instruction, input='', output=truncated_output)
                truncated_prompt, _, _, _, _ = \
                    generate_prompt(data_point,
                                    prompt_type,
                                    prompt_dict,
                                    reduced=True,
                                    making_context=True,
                                    system_prompt=system_prompt,
                                    histi=histi)
                truncated_prompt = remove_refs(truncated_prompt, keep_sources_in_context, langchain_mode, hyde_level,
                                               gradio_errors_to_chatbot)
                truncated_prompt = truncated_prompt.replace('<br>', chat_turn_sep)
                if not truncated_prompt.endswith(chat_turn_sep):
                    truncated_prompt += chat_turn_sep

                if bot is not None:
                    context1 = truncated_prompt + context1

                final_history.insert(0, (truncated_instruction, truncated_output))
                break

            if bot is not None:
                context1 = prompt + context1
            final_history.insert(0, (instruction, output))

        if context1 and not context1.endswith(chat_turn_sep):
            context1 += chat_turn_sep  # Ensure if terminates abruptly, then human continues on next line

    return context1, final_history


def get_relaxed_max_new_tokens(prompt, tokenizer=None, max_new_tokens=None, max_new_tokens0=None):
    # check if can relax max_new_tokens for this specific prompt
    if max_new_tokens0 is not None and \
            hasattr(tokenizer, 'model_max_len') and \
            isinstance(tokenizer.model_max_len, (float, int)):
        max_new_tokens = int(tokenizer.model_max_length) - get_token_count(prompt, tokenizer)
        if max_new_tokens is not None:
            return min(max_new_tokens0, max_new_tokens)
        else:
            return max_new_tokens0
    return max_new_tokens


def get_limited_prompt(instruction,
                       iinput,
                       tokenizer,
                       template_text='',
                       prompter=None,
                       base_model=None,
                       inference_server=None,
                       prompt_type=None, prompt_dict=None, max_new_tokens=None,
                       system_prompt='',
                       allow_chat_system_prompt=None,
                       context='', chat_conversation=None,
                       user_prompt_for_fake_system_prompt=None,
                       text_context_list=None,
                       keep_sources_in_context=False,
                       gradio_errors_to_chatbot=True,
                       model_max_length=None, memory_restriction_level=0,
                       langchain_mode=None, add_chat_history_to_context=True,
                       verbose=False,
                       doc_importance=0.5,
                       hyde_level=None,
                       min_max_new_tokens=512,
                       max_input_tokens=-1,
                       max_total_input_tokens=-1,
                       truncation_generation=False,
                       gradio_server=False,
                       attention_sinks=False,
                       doing_grounding=False,
                       image_file=[],
                       lang_pre_prompt='',
                       lang_prompt='',
                       is_actually_vision_model=False,
                       ):
    """
    Take instruction (estimated_instruction for counting token purposes), iinput, system_prompt, context, chat_conversation, text_context_list as inputs
    and return a prompt and other items accounting for (if required) a balanced truncation of these outputs to avoid going over the token limits
    """

    if gradio_server or not inference_server:
        # can listen to truncation_generation
        pass
    else:
        # these don't support allowing going beyond total context
        truncation_generation = True

    if chat_conversation is None:
        chat_conversation = []

    if not attention_sinks:
        if max_input_tokens >= 0:
            # max_input_tokens is used to runtime (via client/UI) to control actual filling of context
            max_input_tokens = min(model_max_length - min_max_new_tokens, max_input_tokens)
        else:
            max_input_tokens = model_max_length - min_max_new_tokens
    else:
        if max_input_tokens < 0:
            max_input_tokens = model_max_length

    if is_actually_vision_model:
        max_input_tokens -= tokens_per_image(base_model) * len(image_file)

    if prompter:
        prompt_type = prompter.prompt_type
        prompt_dict = prompter.prompt_dict
        stream_output = prompter.stream_output
        system_prompt = prompter.system_prompt
        can_handle_system_prompt = prompter.can_handle_system_prompt
    else:
        can_handle_system_prompt = True  # assume can so no extra conversation added if don't know

    generate_prompt_type = prompt_type
    external_handle_chat_conversation = False
    if inference_server and (any(
            inference_server.startswith(x)
            for x in ['openai_chat', 'openai_azure_chat', 'vllm_chat', 'anthropic', 'google'])) or gradio_server:
        # Chat APIs do not take prompting
        # Replicate does not need prompting if no chat history, but in general can take prompting
        # if using prompter, prompter.system_prompt will already be filled with automatic (e.g. from llama-2),
        # so if replicate final prompt with system prompt still correct because only access prompter.system_prompt that was already set
        # below already true for openai,
        # but not vllm by default as that can be any model and handled by FastChat API inside vLLM itself
        # claude is unique also, by not allowing system prompt, but as conversation
        #   Also in list above, because get_limited_prompt called too late for it in gpt_langchain.py
        #   So needs to be added directly in the get_llm for anthropic there, so used in ExtraChat
        generate_prompt_type = noop_prompt_type
        # Chat APIs don't handle chat history via single prompt, but in messages, assumed to be handled outside this function
        # but we will need to compute good history for external use
        external_handle_chat_conversation = True

    # not if plain prompt, only if unknown or unset
    use_chat_template = get_use_chat_template(tokenizer, prompt_type=prompt_type)
    if is_gradio_vision_model(base_model):
        use_chat_template = False

    if use_chat_template:
        # see if chat template handles system prompt
        if system_prompt in apply_chat_template("Test", system_prompt, [],
                                                tokenizer,
                                                image_file=[],
                                                test_only=True, user_prompt_for_fake_system_prompt=None):
            can_handle_system_prompt = True

        base_size = len(apply_chat_template("Test", None, [],
                                            tokenizer,
                                            image_file=[],
                                            test_only=True, user_prompt_for_fake_system_prompt=None))
    else:
        base_size = 0
    max_input_tokens -= base_size

    context1 = context
    if context1 is None:
        context1 = ''

    from h2oai_pipeline import H2OTextGenerationPipeline
    template_tokens = get_token_count(template_text, tokenizer)
    max_input_tokens -= template_tokens

    ###########################
    # leave bit for instruction regardless of system prompt
    system_prompt0 = system_prompt
    system_prompt, num_system_tokens = H2OTextGenerationPipeline.limit_prompt(system_prompt, tokenizer,
                                                                              max_prompt_length=int(
                                                                                  max_input_tokens * 0.9))
    num_system_tokens0 = num_system_tokens
    max_input_tokens -= num_system_tokens
    if prompter:
        prompter.system_prompt = system_prompt

    lang_prompt, num_system_tokens_a = H2OTextGenerationPipeline.limit_prompt(lang_prompt, tokenizer,
                                                                              max_prompt_length=int(
                                                                                  max_input_tokens * 0.45))
    max_input_tokens -= num_system_tokens_a

    lang_pre_prompt, num_system_tokens_b = H2OTextGenerationPipeline.limit_prompt(lang_pre_prompt, tokenizer,
                                                                                  max_prompt_length=int(
                                                                                      max_input_tokens * 0.45))
    max_input_tokens -= num_system_tokens_b

    # get actual instruction, limited by template limitation
    instruction, num_instruction_tokens = H2OTextGenerationPipeline.limit_prompt(instruction, tokenizer,
                                                                                 max_prompt_length=max_input_tokens)
    max_input_tokens -= num_instruction_tokens

    context1, num_context1_tokens = H2OTextGenerationPipeline.limit_prompt(context1, tokenizer,
                                                                           max_prompt_length=max_input_tokens)
    max_input_tokens -= num_context1_tokens

    iinput, num_iinput_tokens = H2OTextGenerationPipeline.limit_prompt(iinput, tokenizer,
                                                                       max_prompt_length=max_input_tokens)
    max_input_tokens -= num_iinput_tokens

    chat_system_prompt = not external_handle_chat_conversation and \
                         not can_handle_system_prompt and \
                         allow_chat_system_prompt
    if chat_system_prompt and system_prompt:
        user_prompt_for_fake_system_prompt = user_prompt_for_fake_system_prompt or user_prompt_for_fake_system_prompt0
        chat_conversation_system_prompt = [[user_prompt_for_fake_system_prompt, system_prompt]]
        # nuke system prompt else will double-up
        system_prompt = ''
    else:
        chat_conversation_system_prompt = []
    if not gradio_server:
        # else inner calls will handle LLM prompting and system prompt, so don't double up
        chat_conversation = chat_conversation_system_prompt + chat_conversation

    ###########################
    # merge handles if chat_conversation is None
    history = merge_chat_conversation_history(chat_conversation, [])

    history_to_context_func = functools.partial(history_to_context,
                                                langchain_mode=langchain_mode,
                                                add_chat_history_to_context=add_chat_history_to_context,
                                                prompt_type=generate_prompt_type,
                                                prompt_dict=prompt_dict,
                                                # still model_max_length because subtraction done again inside history_to_context
                                                model_max_length=model_max_length,
                                                memory_restriction_level=memory_restriction_level,
                                                keep_sources_in_context=keep_sources_in_context,
                                                #
                                                hyde_level=hyde_level,
                                                gradio_errors_to_chatbot=gradio_errors_to_chatbot,
                                                min_max_new_tokens=min_max_new_tokens)

    ###########################
    # get context2 without history or system_prompt
    if use_chat_template:
        context2 = apply_chat_template(instruction, '', [],
                                       tokenizer,
                                       image_file=image_file,
                                       user_prompt_for_fake_system_prompt=user_prompt_for_fake_system_prompt)
        iinput = ''
        context1 = ''
        num_context1_tokens = 0
        num_context2_tokens = get_token_count(context2, tokenizer)
        num_instruction_tokens0 = num_instruction_tokens
        num_instruction_tokens = 0
        prompt_just_template_tokens = 0
    else:
        context2, _ = history_to_context_func([], system_prompt='')
        context2, num_context2_tokens = H2OTextGenerationPipeline.limit_prompt(context2, tokenizer,
                                                                               max_prompt_length=max_input_tokens)

        # get template size
        data_point = dict(context=' ', instruction=' ', input=' ')
        context_from_history = len(history) > 0
        # if used history -> context2, then already have (if exists) system prompt etc., just get rest of reduced prompt
        reduced = context_from_history
        psave = prompter.system_prompt
        prompter.system__prompt = ' '
        prompt_just_template = prompter.generate_prompt(data_point, context_from_history=context_from_history,
                                                        reduced=reduced,
                                                        image_file=image_file)
        prompter.system_prompt = psave
        prompt_just_template_tokens = get_token_count(prompt_just_template, tokenizer)
        if system_prompt in prompt_just_template:
            prompt_just_template_tokens -= num_system_tokens
        num_context2_tokens += prompt_just_template_tokens

    if text_context_list is None:
        text_context_list = []

    num_doc_overhead_tokens = count_overhead_tokens(tokenizer, doing_grounding=doing_grounding)
    if doing_grounding:
        docs_joiner = "Document xx"
    else:
        docs_joiner = docs_joiner_default
    # handle overhead by lowering locally max input tokens, since not removable
    max_input_tokens -= num_doc_overhead_tokens

    num_doc_tokens0 = sum([get_token_count(x + docs_joiner, tokenizer) for x in text_context_list])

    num_prompt_tokens0 = (num_system_tokens or 0) + \
                         (num_system_tokens_a or 0) + \
                         (num_system_tokens_b or 0) + \
                         (num_instruction_tokens or 0) + \
                         (num_context1_tokens or 0) + \
                         (num_context2_tokens or 0) + \
                         (num_iinput_tokens or 0) + \
                         (num_doc_tokens0 or 0)

    # go down to no less than 256, about 1 paragraph
    # use max_new_tokens before use num_prompt_tokens0 else would be negative or ~0
    min_max_new_tokens = min(min_max_new_tokens, max_new_tokens)

    ###########################
    # reduce docs
    # leave bit for history
    top_k_docs, one_doc_size, num_doc_tokens = get_docs_tokens(tokenizer, text_context_list=text_context_list,
                                                               max_input_tokens=int(max_input_tokens * 0.9))
    max_input_tokens -= num_doc_tokens

    ###########################
    # reduce history given rest of reductions
    history_to_use_final = []
    low, high = 0, len(history) - 1
    best_index = -1  # Keep track of the best index that satisfies the condition
    chat_index = 0
    while low <= high:
        chat_index = (low + high) // 2  # Find the middle index
        if chat_system_prompt and history:  # should always have history[0] but just protection in case
            # Don't ever lose system prompt if putting into chat
            history_to_use = [history[0]] + history[1 + chat_index:]
        else:
            history_to_use = history[0 + chat_index:]

        if use_chat_template:
            context2 = apply_chat_template(instruction, system_prompt, history_to_use,
                                           tokenizer,
                                           image_file=image_file,
                                           user_prompt_for_fake_system_prompt=user_prompt_for_fake_system_prompt)
        else:
            context2, history_to_use = history_to_context_func(history_to_use, system_prompt=system_prompt)

        num_context2_tokens = get_token_count(context2, tokenizer) + prompt_just_template_tokens
        diff1 = max_input_tokens - (
                num_system_tokens + num_system_tokens_a + num_system_tokens_b + num_instruction_tokens + num_context1_tokens + num_context2_tokens)
        if diff1 > 0:
            best_index = chat_index  # Update best index
            # Condition met, try to find if there's a smaller history that still meets the condition
            history_to_use_final = history_to_use.copy()
            high = chat_index - 1
        else:
            # Condition not met, need to include more history
            low = chat_index + 1
        # i.e. if chat_index == len(history), then nothing can be consumed
    if best_index != -1:
        chat_index = best_index
        if chat_system_prompt and history:
            history_to_use_final = [history[0]] + history[1 + best_index:]
        else:
            history_to_use_final = history[0 + best_index:]
    else:
        chat_index = -1
        # can't fit any history
        history_to_use_final = []

    ###########################
    # get final context2
    if use_chat_template:
        context2 = apply_chat_template(instruction, system_prompt, history_to_use_final,
                                       tokenizer,
                                       image_file=image_file,
                                       user_prompt_for_fake_system_prompt=user_prompt_for_fake_system_prompt)
        # now context2 has system tokens
        num_system_tokens = 0
    else:
        context2, history_to_use_final = history_to_context_func(history_to_use_final, system_prompt=system_prompt)

    num_context2_tokens = get_token_count(context2, tokenizer) + prompt_just_template_tokens
    if verbose:
        print("chat_conversation used %d entries out of %d" % (chat_index + 1, len(history)), flush=True)

    # update full context
    # avoid including chat_conversation if handled externally, only used above for computations of prompt
    context = context1 + context2 if not external_handle_chat_conversation else context1

    # update token counts (docs + non-docs, all tokens)
    num_prompt_tokens = (num_system_tokens or 0) + \
                        (num_system_tokens_a or 0) + \
                        (num_system_tokens_b or 0) + \
                        (num_instruction_tokens or 0) + \
                        (num_context1_tokens or 0) + \
                        (num_context2_tokens or 0) + \
                        (num_iinput_tokens or 0) + \
                        (num_doc_tokens or 0)

    # update max_new_tokens
    # limit so max_new_tokens = prompt + new < max
    # otherwise model can fail etc. e.g. for distilgpt2 asking for 1024 tokens is enough to fail if prompt=1 token
    if not attention_sinks:
        max_new_tokens = max(1, min(max_new_tokens, model_max_length - num_prompt_tokens))

    if max_new_tokens < min_max_new_tokens - 30:  # FIXME: fudge factor
        if os.getenv('HARD_ASSERTS'):
            raise ValueError("Invalid max_new_tokens=%s" % max_new_tokens)
        else:
            max_new_tokens = max(32, max_new_tokens)

    if prompter is None:
        # get prompter
        debug = False
        stream_output = False  # doesn't matter
        prompter = Prompter(prompt_type, prompt_dict, debug=debug, stream_output=stream_output,
                            system_prompt=system_prompt, tokenizer=tokenizer, base_model=base_model)
        if prompt_type != generate_prompt_type:
            # override just this attribute, keep system_prompt etc. from original prompt_type
            prompter.prompt_type = generate_prompt_type

    if not use_chat_template:
        data_point = dict(context=context, instruction=instruction, input=iinput)
        # handle promptA/promptB addition if really from history.
        # if not from history, then reduced=False inside correct
        # if mixed, then no specific correct thing to do, so treat like history and promptA/B will come first still
        context_from_history = len(history) > 0
        # if used history -> context2, then already have (if exists) system prompt etc., just get rest of reduced prompt
        reduced = context_from_history
        prompt = prompter.generate_prompt(data_point, context_from_history=context_from_history, reduced=reduced,
                                          image_file=image_file)
    else:
        # assume inner gradio server handles.  if we point to gradio server (i.e. gradio_server=True) then we just pass instruction
        prompt = instruction if gradio_server else context2
        if gradio_server and not prompter.can_handle_system_prompt and system_prompt:
            # then must have added in pre-conversation, remove for inner gradio to handle, here we just wanted to count accurately
            if history_to_use_final and history_to_use_final[0][1] == system_prompt:
                # protection just in case logic isn't perfect
                history_to_use_final.pop(0)

    num_prompt_tokens_actual = get_token_count(prompt, tokenizer)

    if chat_system_prompt and system_prompt:
        system_prompt_return = system_prompt0
    else:
        system_prompt_return = system_prompt

    return prompt, \
        instruction, iinput, context, \
        num_prompt_tokens, max_new_tokens, num_prompt_tokens0, num_prompt_tokens_actual, \
        history_to_use_final, external_handle_chat_conversation, \
        top_k_docs, one_doc_size, truncation_generation, \
        system_prompt_return, lang_pre_prompt, lang_prompt


def count_overhead_tokens(tokenizer, doing_grounding=False):
    if doing_grounding:
        from openai_server.backend_utils import structure_to_messages
        system_prompt = ''
        instruction = 'foo'
        chat_conversation = []
        image_file = []
        prompt = tokenizer.apply_grounded_generation_template(
            structure_to_messages(instruction,
                                  system_prompt if system_prompt not in [None, '', 'auto'] else None,
                                  chat_conversation,
                                  image_file),
            documents=[dict(text='foo')],
            citation_mode="accurate",  # or "fast"
            tokenize=False,
            add_generation_prompt=True,
        )
        return get_token_count(prompt, tokenizer)
    else:
        return 0


def entrypoint_main():
    """
    Examples:

    WORLD_SIZE=4 CUDA_VISIBLE_DEVICES="0,1,2,3" torchrun --nproc_per_node=4 --master_port=1234 generate.py --base_model='EleutherAI/gpt-j-6B' --lora_weights=lora-alpaca_6B
    python generate.py --base_model='EleutherAI/gpt-j-6B' --lora_weights='lora-alpaca_6B'
    python generate.py --base_model='EleutherAI/gpt-neox-20b' --lora_weights='lora-alpaca_20B'

    # generate without lora weights, no prompt
    python generate.py --base_model='EleutherAI/gpt-neox-20b' --prompt_type='plain'
    python generate.py --base_model='togethercomputer/GPT-NeoXT-Chat-Base-20B' --prompt_type='dai_faq'

    python generate.py --base_model='togethercomputer/GPT-NeoXT-Chat-Base-20B' --prompt_type='dai_faq' --lora_weights='lora_20B_daifaq'
    # OpenChatKit settings:
    python generate.py --base_model='togethercomputer/GPT-NeoXT-Chat-Base-20B' --prompt_type='human_bot --debug=True --num_beams=1 --temperature=0.6 --top_k=40 --top_p=1.0

    python generate.py --base_model='distilgpt2' --prompt_type='plain' --debug=True --num_beams=1 --temperature=0.6 --top_k=40 --top_p=1.0 --share=False
    python generate.py --base_model='t5-large' --prompt_type='simple_instruct'
    python generate.py --base_model='philschmid/bart-large-cnn-samsum'
    python generate.py --base_model='philschmid/flan-t5-base-samsum'
    python generate.py --base_model='facebook/mbart-large-50-many-to-many-mmt'

    python generate.py --base_model='togethercomputer/GPT-NeoXT-Chat-Base-20B' --prompt_type='human_bot' --lora_weights='GPT-NeoXT-Chat-Base-20B.merged.json.8_epochs.57b2892c53df5b8cefac45f84d019cace803ef26.28'

    must have 4*48GB GPU and run without 8bit in order for sharding to work with use_gpu_id=False
    can also pass --prompt_type='human_bot' and model can somewhat handle instructions without being instruct tuned
    python generate.py --base_model=decapoda-research/llama-65b-hf --load_8bit=False --use_gpu_id=False --prompt_type='human_bot'

    python generate.py --base_model=h2oai/h2ogpt-oig-oasst1-512-6_9b
    """
    H2O_Fire(main)


def append_certificates(certs_dir):
    import certifi
    cert_bundle_path = certifi.where()

    default_bundle_from_helm = "/etc/ssl/certs/root-ca-bundle.crt"
    ssl_cache_dir = os.getenv("SSL_CACHE_DIR", ".cache/.ssl_cache")
    ssl_cache_dir = os.path.abspath(makedirs(ssl_cache_dir, exist_ok=True, tmp_ok=True, use_base=True))
    output_file = os.path.join(ssl_cache_dir, "ca-bundle.pem")

    with open(cert_bundle_path, 'r') as bundle_file:
        bundle_content = bundle_file.read()

    combined_cert_content = bundle_content

    additional_certs_found = False
    if certs_dir:
        for root, _, files in os.walk(certs_dir):
            for file in files:
                if file.endswith(('.crt', '.pem')):
                    cert_file_path = os.path.join(root, file)
                    print(f"adding cert {os.path.abspath(cert_file_path)}")
                    with open(cert_file_path, 'r') as cert:
                        combined_cert_content += '\n' + cert.read()
                    additional_certs_found = True

    if os.path.exists(default_bundle_from_helm) and os.path.isfile(default_bundle_from_helm):
        print(f"adding default helm cert {default_bundle_from_helm}")
        with open(default_bundle_from_helm, 'r') as cert:
            combined_cert_content += '\n' + cert.read()
        additional_certs_found = True

    if additional_certs_found:
        with open(output_file, 'w') as output:
            output.write(combined_cert_content)

        os.environ['SSL_CERT_FILE'] = output_file
        print(f"Combined certificate file created at: {output_file}")


if __name__ == "__main__":
    entrypoint_main()
