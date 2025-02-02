import ast
import functools
import json
import os
import sys
import tempfile
import time
import typing
import uuid

import pytest

from tests.utils import wrap_test_forked
from src.prompter_utils import base64_encode_jinja_template, base64_decode_jinja_template
from src.vision.utils_vision import process_file_list
from src.utils import get_list_or_str, read_popen_pipes, get_token_count, reverse_ucurve_list, undo_reverse_ucurve_list, \
    is_uuid4, has_starting_code_block, extract_code_block_content, looks_like_json, get_json, is_full_git_hash, \
    deduplicate_names, handle_json, check_input_type, start_faulthandler, remove, get_gradio_depth, create_typed_dict, \
    execute_cmd_stream
from src.enums import invalid_json_str, user_prompt_for_fake_system_prompt0
from src.prompter import apply_chat_template
import subprocess as sp

start_faulthandler()


@wrap_test_forked
def test_get_list_or_str():
    assert get_list_or_str(['foo', 'bar']) == ['foo', 'bar']
    assert get_list_or_str('foo') == 'foo'
    assert get_list_or_str("['foo', 'bar']") == ['foo', 'bar']


@wrap_test_forked
def test_stream_popen1():
    cmd_python = sys.executable
    python_args = "-q -u"
    python_code = "print('hi')"

    cmd = f"{cmd_python} {python_args} -c \"{python_code}\""

    with sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.PIPE, text=True, shell=True) as p:
        for out_line, err_line in read_popen_pipes(p):
            print(out_line, end='')
            print(err_line, end='')

        p.poll()


@wrap_test_forked
def test_stream_popen2():
    script = """for i in 0 1 2 3 4 5
do
    echo "This messages goes to stdout $i"
    sleep 1
    echo This message goes to stderr >&2
    sleep 1
done
"""
    with open('pieces.sh', 'wt') as f:
        f.write(script)
    os.chmod('pieces.sh', 0o755)
    with sp.Popen(["./pieces.sh"], stdout=sp.PIPE, stderr=sp.PIPE, text=True, shell=True) as p:
        for out_line, err_line in read_popen_pipes(p):
            print(out_line, end='')
            print(err_line, end='')
        p.poll()


@wrap_test_forked
def test_stream_python_execution(capsys):
    script = """
import sys
import time
for i in range(3):
    print(f"This message goes to stdout {i}")
    time.sleep(0.1)
    print(f"This message goes to stderr {i}", file=sys.stderr)
    time.sleep(0.1)
"""

    result = execute_cmd_stream(
        script_content=script,
        cwd=None,
        env=None,
        timeout=5,
        capture_output=True,
        text=True,
        print_tags=True,
        print_literal=False,
    )

    # Capture the printed output
    captured = capsys.readouterr()

    # Print the captured output for verification
    print("Captured output:")
    print(captured.out)

    # Check return code
    assert result.returncode == 0, f"Expected return code 0, but got {result.returncode}"

    # Check stdout content
    expected_stdout = "This message goes to stdout 0\nThis message goes to stdout 1\nThis message goes to stdout 2\n"
    assert expected_stdout in result.stdout, f"Expected stdout to contain:\n{expected_stdout}\nBut got:\n{result.stdout}"

    # Check stderr content
    expected_stderr = "This message goes to stderr 0\nThis message goes to stderr 1\nThis message goes to stderr 2\n"
    assert expected_stderr in result.stderr, f"Expected stderr to contain:\n{expected_stderr}\nBut got:\n{result.stderr}"

    # Check if the output was streamed (should appear in captured output)
    assert "STDOUT: This message goes to stdout 0" in captured.out, "Streaming output not detected in stdout"
    assert "STDERR: This message goes to stderr 0" in captured.out, "Streaming output not detected in stderr"

    print("All tests passed successfully!")


def test_stream_python_execution_empty_lines(capsys):
    script = """
import sys
import time
print()
print("Hello")
print()
print("World", file=sys.stderr)
print()
"""

    result = execute_cmd_stream(
        script_content=script,
        cwd=None,
        env=None,
        timeout=5,
        capture_output=True,
        text=True
    )

    captured = capsys.readouterr()

    print("Captured output:")
    print(captured.out)

    # Check that we only see STDOUT and STDERR for non-empty lines
    assert captured.out.count("STDOUT:") == 1, "Expected only one STDOUT line"
    assert captured.out.count("STDERR:") == 1, "Expected only one STDERR line"
    assert "STDOUT: Hello" in captured.out, "Expected 'Hello' in stdout"
    assert "STDERR: World" in captured.out, "Expected 'World' in stderr"

    print("All tests passed successfully!")


@wrap_test_forked
def test_memory_limit():
    result = execute_cmd_stream(cmd=['python', './tests/memory_hog_script.py'], max_memory_usage=500_000_000)
    assert result.returncode == -15
    print(result.stdout, file=sys.stderr, flush=True)
    print(result.stderr, file=sys.stderr, flush=True)


@pytest.mark.parametrize("text_context_list",
                         ['text_context_list1', 'text_context_list2', 'text_context_list3', 'text_context_list4',
                          'text_context_list5', 'text_context_list6'])
@pytest.mark.parametrize("system_prompt", ['auto', ''])
@pytest.mark.parametrize("context", ['context1', 'context2'])
@pytest.mark.parametrize("iinput", ['iinput1', 'iinput2'])
@pytest.mark.parametrize("chat_conversation", ['chat_conversation1', 'chat_conversation2'])
@pytest.mark.parametrize("instruction", ['instruction1', 'instruction2'])
@wrap_test_forked
def test_limited_prompt(instruction, chat_conversation, iinput, context, system_prompt, text_context_list):
    instruction1 = 'Who are you?'
    instruction2 = ' '.join(['foo_%s ' % x for x in range(0, 500)])
    instruction = instruction1 if instruction == 'instruction1' else instruction2

    iinput1 = 'Extra instruction info'
    iinput2 = ' '.join(['iinput_%s ' % x for x in range(0, 500)])
    iinput = iinput1 if iinput == 'iinput1' else iinput2

    context1 = 'context'
    context2 = ' '.join(['context_%s ' % x for x in range(0, 500)])
    context = context1 if context == 'context1' else context2

    chat_conversation1 = []
    chat_conversation2 = [['user_conv_%s ' % x, 'bot_conv_%s ' % x] for x in range(0, 500)]
    chat_conversation = chat_conversation1 if chat_conversation == 'chat_conversation1' else chat_conversation2

    text_context_list1 = []
    text_context_list2 = ['doc_%s ' % x for x in range(0, 500)]
    text_context_list3 = ['doc_%s ' % x for x in range(0, 10)]
    text_context_list4 = ['documentmany_%s ' % x for x in range(0, 10000)]
    import random, string
    text_context_list5 = [
        'documentlong_%s_%s' % (x, ''.join(random.choices(string.ascii_letters + string.digits, k=300))) for x in
        range(0, 20)]
    text_context_list6 = [
        'documentlong_%s_%s' % (x, ''.join(random.choices(string.ascii_letters + string.digits, k=4000))) for x in
        range(0, 1)]
    if text_context_list == 'text_context_list1':
        text_context_list = text_context_list1
    elif text_context_list == 'text_context_list2':
        text_context_list = text_context_list2
    elif text_context_list == 'text_context_list3':
        text_context_list = text_context_list3
    elif text_context_list == 'text_context_list4':
        text_context_list = text_context_list4
    elif text_context_list == 'text_context_list5':
        text_context_list = text_context_list5
    elif text_context_list == 'text_context_list6':
        text_context_list = text_context_list6
    else:
        raise ValueError("No such %s" % text_context_list)

    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained('h2oai/h2ogpt-4096-llama2-7b-chat')

    prompt_type = 'llama2'
    prompt_dict = None
    debug = False
    chat = True
    stream_output = True
    from src.prompter import Prompter
    prompter = Prompter(prompt_type, prompt_dict, debug=debug,
                        stream_output=stream_output,
                        system_prompt=system_prompt,
                        tokenizer=tokenizer)

    min_max_new_tokens = 512  # like in get_limited_prompt()
    max_input_tokens = -1
    max_new_tokens = 1024
    model_max_length = 4096

    from src.gen import get_limited_prompt
    estimated_full_prompt, \
        instruction, iinput, context, \
        num_prompt_tokens, max_new_tokens, \
        num_prompt_tokens0, num_prompt_tokens_actual, \
        history_to_use_final, external_handle_chat_conversation, \
        top_k_docs_trial, one_doc_size, truncation_generation, system_prompt, _, _ = \
        get_limited_prompt(instruction, iinput, tokenizer,
                           prompter=prompter,
                           max_new_tokens=max_new_tokens,
                           context=context,
                           chat_conversation=chat_conversation,
                           text_context_list=text_context_list,
                           model_max_length=model_max_length,
                           min_max_new_tokens=min_max_new_tokens,
                           max_input_tokens=max_input_tokens,
                           verbose=True)
    print('%s -> %s or %s: len(history_to_use_final): %s top_k_docs_trial=%s one_doc_size: %s' % (num_prompt_tokens0,
                                                                                                  num_prompt_tokens,
                                                                                                  num_prompt_tokens_actual,
                                                                                                  len(history_to_use_final),
                                                                                                  top_k_docs_trial,
                                                                                                  one_doc_size),
          flush=True, file=sys.stderr)
    assert num_prompt_tokens <= model_max_length + min_max_new_tokens
    # actual might be less due to token merging for characters across parts, but not more
    assert num_prompt_tokens >= num_prompt_tokens_actual
    assert num_prompt_tokens_actual <= model_max_length

    if top_k_docs_trial > 0:
        text_context_list = text_context_list[:top_k_docs_trial]
    elif one_doc_size is not None:
        text_context_list = [text_context_list[0][:one_doc_size]]
    else:
        text_context_list = []
    assert sum([get_token_count(x, tokenizer) for x in text_context_list]) <= model_max_length


@wrap_test_forked
def test_reverse_ucurve():
    ab = []
    a = [1, 2, 3, 4, 5, 6, 7, 8]
    b = [2, 4, 6, 8, 7, 5, 3, 1]
    ab.append([a, b])
    a = [1]
    b = [1]
    ab.append([a, b])
    a = [1, 2]
    b = [2, 1]
    ab.append([a, b])
    a = [1, 2, 3]
    b = [2, 3, 1]
    ab.append([a, b])
    a = [1, 2, 3, 4]
    b = [2, 4, 3, 1]
    ab.append([a, b])

    for a, b in ab:
        assert reverse_ucurve_list(a) == b
        assert undo_reverse_ucurve_list(b) == a


@wrap_test_forked
def check_gradio():
    import gradio as gr
    assert gr.__h2oai__


@wrap_test_forked
def test_is_uuid4():
    # Example usage:
    test_strings = [
        "f47ac10b-58cc-4372-a567-0e02b2c3d479",  # Valid UUID v4
        "not-a-uuid",  # Invalid
        "12345678-1234-1234-1234-123456789abc",  # Valid UUID v4
        "xyz"  # Invalid
    ]
    # "f47ac10b-58cc-4372-a567-0e02b2c3d479": True (Valid UUID v4)
    # "not-a-uuid": False (Invalid)
    # "12345678-1234-1234-1234-123456789abc": False (Invalid, even though it resembles a UUID, it doesn't follow the version 4 UUID pattern)
    # "xyz": False (Invalid)

    # Check each string and print whether it's a valid UUID v4
    assert [is_uuid4(s) for s in test_strings] == [True, False, False, False]


@wrap_test_forked
def test_is_git_hash():
    # Example usage:
    hashes = ["1a3b5c7d9e1a3b5c7d9e1a3b5c7d9e1a3b5c7d9e", "1G3b5c7d9e1a3b5c7d9e1a3b5c7d9e1a3b5c7d9e", "1a3b5c7d"]

    assert [is_full_git_hash(h) for h in hashes] == [True, False, False]


@wrap_test_forked
def test_chat_template():
    instruction = "Who are you?"
    system_prompt = "Be kind"
    history_to_use = [('Are you awesome?', "Yes I'm awesome.")]
    image_file = []
    other_base_models = ['h2oai/mixtral-gm-rag-experimental-v2']
    supports_system_prompt = ['meta-llama/Llama-2-7b-chat-hf', 'openchat/openchat-3.5-1210', 'SeaLLMs/SeaLLM-7B-v2',
                              'h2oai/h2ogpt-gm-experimental']
    base_models = supports_system_prompt + other_base_models

    for base_model in base_models:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(base_model)

        prompt = apply_chat_template(instruction, system_prompt, history_to_use, image_file,
                                     tokenizer,
                                     user_prompt_for_fake_system_prompt=user_prompt_for_fake_system_prompt0,
                                     verbose=True)

        assert 'Be kind' in prompt  # put into pre-conversation if no actual system prompt
        assert instruction in prompt
        assert history_to_use[0][0] in prompt
        assert history_to_use[0][1] in prompt


@wrap_test_forked
def test_chat_template_images():
    history_to_use = [('Are you awesome?', "Yes I'm awesome.")]
    base_model = 'OpenGVLab/InternVL-Chat-V1-5'

    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)

    messages = [{'role': 'system',
                 'content': 'You are h2oGPTe, an expert question-answering AI system created by H2O.ai that performs like GPT-4 by OpenAI.'},
                {'role': 'user',
                 'content': 'What is the name of the tower in one of the images?'}]
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    assert prompt is not None

    (instruction, system_prompt, chat_conversation, image_file,
     user_prompt_for_fake_system_prompt,
     test_only, verbose) = ('What is the name of the tower in one of the images?',
                            'You are h2oGPTe, an expert question-answering AI system created by H2O.ai that performs like GPT-4 by OpenAI.',
                            [], ['/tmp/image_file_0f5f011d-c907-4836-9f38-0ba579b45ffc.jpeg',
                                 '/tmp/image_file_60dce245-af39-4f8c-9651-df9ae0bd0afa.jpeg',
                                 '/tmp/image_file_e0b32625-9de3-40d7-98fb-c2e6368d6d73.jpeg'], None, False, False)

    prompt = apply_chat_template(instruction, system_prompt, history_to_use, image_file,
                                 tokenizer,
                                 user_prompt_for_fake_system_prompt=user_prompt_for_fake_system_prompt0,
                                 test_only=test_only,
                                 verbose=verbose)

    assert 'h2oGPTe' in prompt  # put into pre-conversation if no actual system prompt
    assert instruction in prompt
    assert history_to_use[0][0] in prompt
    assert history_to_use[0][1] in prompt


@wrap_test_forked
def test_partial_codeblock():
    json.dumps(invalid_json_str)

    # Example usages:
    example_1 = "```code block starts immediately"
    example_2 = "\n    ```code block after newline and spaces"
    example_3 = "<br>```code block after HTML line break"
    example_4 = "This is a regular text without a code block."

    assert has_starting_code_block(example_1)
    assert has_starting_code_block(example_2)
    assert has_starting_code_block(example_3)
    assert not has_starting_code_block(example_4)

    # Example usages:
    example_stream_1 = "```code block content here```more text"
    example_stream_2 = "```code block content with no end yet..."
    example_stream_3 = "```\ncode block content here\n```\nmore text"
    example_stream_4 = "```\ncode block content \nwith no end yet..."
    example_stream_5 = "\n ```\ncode block content here\n```\nmore text"
    example_stream_6 = "\n ```\ncode block content \nwith no end yet..."

    example_stream_7 = "more text"

    assert extract_code_block_content(example_stream_1) == "block content here"
    assert extract_code_block_content(example_stream_2) == "block content with no end yet..."
    assert extract_code_block_content(example_stream_3) == "code block content here"
    assert extract_code_block_content(example_stream_4) == "code block content \nwith no end yet..."
    assert extract_code_block_content(example_stream_5) == "code block content here"
    assert extract_code_block_content(example_stream_6) == "code block content \nwith no end yet..."
    assert extract_code_block_content(example_stream_7) == ""

    # Assuming the function extract_code_block_content is defined as previously described.

    # Test case 1: Empty string
    assert extract_code_block_content("") is '', "Test 1 Failed: Should return None for empty string"

    # Test case 2: No starting code block
    assert extract_code_block_content(
        "No code block here") is '', "Test 2 Failed: Should return None if there's no starting code block"

    # Test case 3: Code block at the start without ending
    assert extract_code_block_content(
        "```text\nStarting without end") == "Starting without end", "Test 3 Failed: Should return the content of code block starting at the beginning"

    # Test case 4: Code block at the end without starting
    assert extract_code_block_content(
        "Text before code block```text\nEnding without start") == "Ending without start", "Test 4 Failed: Should extract text following starting delimiter regardless of position"

    # Test case 5: Code block in the middle with proper closing
    assert extract_code_block_content(
        "Text before ```text\ncode block``` text after") == "code block", "Test 5 Failed: Should extract the code block in the middle"

    # Test case 6: Multiple code blocks, only extracts the first one
    assert extract_code_block_content(
        "```text\nFirst code block``` Text in between ```Second code block```") == "First code block", "Test 6 Failed: Should only extract the first code block"

    # Test case 7: Code block with only whitespace inside
    assert extract_code_block_content(
        "```   ```") == "", "Test 7 Failed: Should return an empty string for a code block with only whitespace"

    # Test case 8: Newline characters inside code block
    assert extract_code_block_content(
        "```\nLine 1\nLine 2\n```") == "Line 1\nLine 2", "Test 8 Failed: Should preserve newline characters within code block but not leading/trailing newlines due to .strip()"

    # Test case 9: Code block with special characters
    special_characters = "```text\nSpecial characters !@#$%^&*()```"
    assert extract_code_block_content(
        special_characters) == "Special characters !@#$%^&*()", "Test 9 Failed: Should correctly handle special characters"

    # Test case 10: No starting code block but with ending delimiter
    assert extract_code_block_content(
        "Text with ending code block delimiter```") is '', "Test 10 Failed: Should return None if there's no starting code block but with an ending delimiter"

    # Test cases
    assert looks_like_json('{ "key": "value" }'), "Failed: JSON object"
    assert looks_like_json('[1, 2, 3]'), "Failed: JSON array"
    assert looks_like_json(' "string" '), "Failed: JSON string"
    assert looks_like_json('null'), "Failed: JSON null"
    assert looks_like_json(' true '), "Failed: JSON true"
    assert looks_like_json('123'), "Failed: JSON number"
    assert not looks_like_json('Just a plain text'), "Failed: Not JSON"
    assert not looks_like_json('```code block```'), "Failed: Code block"

    # Test cases
    get_json_nofixup = functools.partial(get_json, fixup=False)
    assert get_json_nofixup(
        '{"key": "value"}') == '{"key": "value"}', "Failed: Valid JSON object should be returned as is."
    assert get_json_nofixup('[1, 2, 3]') == '[1, 2, 3]', "Failed: Valid JSON array should be returned as is."
    assert get_json_nofixup('```text\nSome code```') == 'Some code', "Failed: Code block content should be returned."
    assert get_json_nofixup(
        'Some random text') == invalid_json_str, "Failed: Random text should lead to 'invalid json' return."
    assert get_json_nofixup(
        '```{"key": "value in code block"}```') == '{"key": "value in code block"}', "Failed: JSON in code block should be correctly extracted and returned."
    assert get_json_nofixup(
        '```code\nmore code```') == 'more code', "Failed: Multi-line code block content should be returned."
    assert get_json_nofixup(
        '```\n{"key": "value"}\n```') == '{"key": "value"}', "Failed: JSON object in code block with new lines should be correctly extracted and returned."
    assert get_json_nofixup('') == invalid_json_str, "Failed: Empty string should lead to 'invalid json' return."
    assert get_json_nofixup(
        'True') == invalid_json_str, "Failed: Non-JSON 'True' value should lead to 'invalid json' return."
    assert get_json_nofixup(
        '{"incomplete": true,') == '{"incomplete": true,', "Failed: Incomplete JSON should still be considered as JSON and returned as is."

    answer = """Here is an example JSON that fits the provided schema:
```json
{
  "name": "John Doe",
  "age": 30,
  "skills": ["Java", "Python", "JavaScript"],
  "work history": [
    {
      "company": "ABC Corp",
      "duration": "2018-2020",
      "position": "Software Engineer"
    },
    {
      "company": "XYZ Inc",
      "position": "Senior Software Engineer",
      "duration": "2020-Present"
    }
  ]
}
```
Note that the `work history` array contains two objects, each with a `company`, `duration`, and `position` property. The `skills` array contains three string elements, each with a maximum length of 10 characters. The `name` and `age` properties are also present and are of the correct data types."""
    assert get_json_nofixup(answer) == """{
  "name": "John Doe",
  "age": 30,
  "skills": ["Java", "Python", "JavaScript"],
  "work history": [
    {
      "company": "ABC Corp",
      "duration": "2018-2020",
      "position": "Software Engineer"
    },
    {
      "company": "XYZ Inc",
      "position": "Senior Software Engineer",
      "duration": "2020-Present"
    }
  ]
}"""

    # JSON within a code block
    json_in_code_block = """
    Here is an example JSON:
    ```json
    {"key": "value"}
    ```
    """

    # Plain JSON response
    plain_json_response = '{"key": "value"}'

    # Invalid JSON or non-JSON response
    non_json_response = "This is just some text."

    # Tests
    assert get_json_nofixup(
        json_in_code_block).strip() == '{"key": "value"}', "Should extract and return JSON from a code block."
    assert get_json_nofixup(plain_json_response) == '{"key": "value"}', "Should return plain JSON as is."
    assert get_json_nofixup(
        non_json_response) == invalid_json_str, "Should return 'invalid json' for non-JSON response."

    # Test with the provided example
    stream_content = """ {\n \"name\": \"John Doe\",\n \"email\": \"john.doe@example.com\",\n \"jobTitle\": \"Software Developer\",\n \"department\": \"Technology\",\n \"hireDate\": \"2020-01-01\",\n \"employeeId\": 123456,\n \"manager\": {\n \"name\": \"Jane Smith\",\n \"email\": \"jane.smith@example.com\",\n \"jobTitle\": \"Senior Software Developer\"\n },\n \"skills\": [\n \"Java\",\n \"Python\",\n \"JavaScript\",\n \"React\",\n \"Spring\"\n ],\n \"education\": {\n \"degree\": \"Bachelor's Degree\",\n \"field\": \"Computer Science\",\n \"institution\": \"Example University\",\n \"graduationYear\": 2018\n },\n \"awards\": [\n {\n \"awardName\": \"Best Developer of the Year\",\n \"year\": 2021\n },\n {\n \"awardName\": \"Most Valuable Team Player\",\n \"year\": 2020\n }\n ],\n \"performanceRatings\": {\n \"communication\": 4.5,\n \"teamwork\": 4.8,\n \"creativity\": 4.2,\n \"problem-solving\": 4.6,\n \"technical skills\": 4.7\n }\n}\n```"""
    extracted_content = get_json_nofixup(stream_content)
    assert extracted_content == """{\n \"name\": \"John Doe\",\n \"email\": \"john.doe@example.com\",\n \"jobTitle\": \"Software Developer\",\n \"department\": \"Technology\",\n \"hireDate\": \"2020-01-01\",\n \"employeeId\": 123456,\n \"manager\": {\n \"name\": \"Jane Smith\",\n \"email\": \"jane.smith@example.com\",\n \"jobTitle\": \"Senior Software Developer\"\n },\n \"skills\": [\n \"Java\",\n \"Python\",\n \"JavaScript\",\n \"React\",\n \"Spring\"\n ],\n \"education\": {\n \"degree\": \"Bachelor's Degree\",\n \"field\": \"Computer Science\",\n \"institution\": \"Example University\",\n \"graduationYear\": 2018\n },\n \"awards\": [\n {\n \"awardName\": \"Best Developer of the Year\",\n \"year\": 2021\n },\n {\n \"awardName\": \"Most Valuable Team Player\",\n \"year\": 2020\n }\n ],\n \"performanceRatings\": {\n \"communication\": 4.5,\n \"teamwork\": 4.8,\n \"creativity\": 4.2,\n \"problem-solving\": 4.6,\n \"technical skills\": 4.7\n }\n}"""


def test_partial_codeblock2():
    example_1 = "```code block starts immediately"
    example_2 = "\n    ```code block after newline and spaces"
    example_3 = "<br>```code block after HTML line break"
    example_4 = "This is a regular text without a code block."

    assert has_starting_code_block(example_1)
    assert has_starting_code_block(example_2)
    assert has_starting_code_block(example_3)
    assert not has_starting_code_block(example_4)


def test_extract_code_block_content():
    example_stream_1 = "```code block content here```more text"
    example_stream_2 = "```code block content with no end yet..."
    example_stream_3 = "```\ncode block content here\n```\nmore text"
    example_stream_4 = "```\ncode block content \nwith no end yet..."
    example_stream_5 = "\n ```\ncode block content here\n```\nmore text"
    example_stream_6 = "\n ```\ncode block content \nwith no end yet..."
    example_stream_7 = "more text"
    example_stream_8 = """```markdown
```json
{
  "Employee": {
    "Name": "Henry",
    "Title": "AI Scientist",
    "Department": "AI",
    "Location": "San Francisco",
    "Contact": {
      "Email": "henryai@gmail.com",
      "Phone": "+1-234-567-8901"
    },
    "Profile": {
      "Education": [
        {
          "Institution": "Stanford University",
          "Degree": "Ph.D.",
          "Field": "Computer Science"
        },
        {
          "Institution": "University of California, Berkeley",
          "Degree": "M.S.",
          "Field": "Artificial Intelligence"
        }
      ],
      "Experience": [
        {
          "Company": "Google",
          "Role": "Senior AI Engineer",
          "Duration": "5 years"
        },
        {
          "Company": "Facebook",
          "Role": "Principal AI Engineer",
          "Duration": "3 years"
        }
      ],
      "Skills": [
        "Python",
        "TensorFlow",
        "PyTorch",
        "Natural Language Processing",
        "Machine Learning"
      ],
      "Languages": [
        "English",
        "French",
        "Spanish"
      ],
      "Certifications": [
        {
          "Name": "Certified AI Professional",
          "Issuing Body": "AI Professional Association"
        },
        {
          "Name": "Advanced AI Course Certificate",
          "Issuing Body": "AI Institute"
        }
      ]
    }
  }
}
```
"""
    assert extract_code_block_content(example_stream_1) == "block content here"
    assert extract_code_block_content(example_stream_2) == "block content with no end yet..."
    assert extract_code_block_content(example_stream_3) == "code block content here"
    assert extract_code_block_content(example_stream_4) == "code block content \nwith no end yet..."
    assert extract_code_block_content(example_stream_5) == "code block content here"
    assert extract_code_block_content(example_stream_6) == "code block content \nwith no end yet..."
    assert extract_code_block_content(example_stream_7) == ""
    expected8 = """{
  "Employee": {
    "Name": "Henry",
    "Title": "AI Scientist",
    "Department": "AI",
    "Location": "San Francisco",
    "Contact": {
      "Email": "henryai@gmail.com",
      "Phone": "+1-234-567-8901"
    },
    "Profile": {
      "Education": [
        {
          "Institution": "Stanford University",
          "Degree": "Ph.D.",
          "Field": "Computer Science"
        },
        {
          "Institution": "University of California, Berkeley",
          "Degree": "M.S.",
          "Field": "Artificial Intelligence"
        }
      ],
      "Experience": [
        {
          "Company": "Google",
          "Role": "Senior AI Engineer",
          "Duration": "5 years"
        },
        {
          "Company": "Facebook",
          "Role": "Principal AI Engineer",
          "Duration": "3 years"
        }
      ],
      "Skills": [
        "Python",
        "TensorFlow",
        "PyTorch",
        "Natural Language Processing",
        "Machine Learning"
      ],
      "Languages": [
        "English",
        "French",
        "Spanish"
      ],
      "Certifications": [
        {
          "Name": "Certified AI Professional",
          "Issuing Body": "AI Professional Association"
        },
        {
          "Name": "Advanced AI Course Certificate",
          "Issuing Body": "AI Institute"
        }
      ]
    }
  }
}"""
    assert extract_code_block_content(example_stream_8) == expected8


@pytest.mark.parametrize("method", ['repair_json', 'get_json'])
@wrap_test_forked
def test_repair_json(method):
    a = """{
    "Supplementary Leverage Ratio": [7.0, 5.8, 5.7],
    "Liquidity Metrics": {
    "End of Period Liabilities and Equity": [2260, 2362, 2291],
    "Liquidity Coverage Ratio": [118, 115, 115],
    "Trading-Related Liabilities(7)": [84, 72, 72],
    "Total Available Liquidty Resources": [972, 994, 961],
    "Deposits Balance Sheet": [140, 166, 164],
    "Other Liabilities(7)": {},
    "LTD": {},
    "Equity": {
    "Book Value per share": [86.43, 92.16, 92.21],
    "Tangible Book Value per share": [73.67, 79.07, 79.16]
    }
    },
    "Capital and Balance Sheet ($ in B)": {
    "Risk-based Capital Metrics(1)": {
    "End of Period Assets": [2260, 2362, 2291],
    "CET1 Capital": [147, 150, 150],
    "Standardized RWAs": [1222, 1284, 1224],
    "Investments, net": {},
    "CET1 Capital Ratio - Standardized": [12.1, 11.7, 12.2],
    "Advanced RWAs": [1255, 1265, 1212],
    "Trading-Related Assets(5)": [670, 681, 659],
    "CET1 Capital Ratio - Advanced": [11.7, 11.8, 12.4],
    "Loans, net(6)": {},
    "Other(5)": [182, 210, 206]
    }
    }
    }
    
    Note: Totals may not sum due to rounding. LTD: Long-term debt. All information for 4Q21 is preliminary. All footnotes are presented on Slide 26."""

    from json_repair import repair_json

    for i in range(len(a)):
        text = a[:i]
        t0 = time.time()
        if method == 'repair_json':
            good_json_string = repair_json(text)
        else:
            good_json_string = get_json(text)
        if i > 50:
            assert len(good_json_string) > 5
        tdelta = time.time() - t0
        assert tdelta < 0.005, "Too slow: %s" % tdelta
        print("%s : %s : %s" % (i, tdelta, good_json_string))
        json.loads(good_json_string)


def test_json_repair_more():
    response0 = """```markdown
    ```json
    {
      "Employee": {
        "Name": "Henry",
        "Title": "AI Scientist",
        "Department": "AI",
        "Location": "San Francisco",
        "Contact": {
          "Email": "henryai@gmail.com",
          "Phone": "+1-234-567-8901"
        },
        "Profile": {
          "Education": [
            {
              "Institution": "Stanford University",
              "Degree": "Ph.D.",
              "Field": "Computer Science"
            },
            {
              "Institution": "University of California, Berkeley",
              "Degree": "M.S.",
              "Field": "Artificial Intelligence"
            }
          ],
          "Experience": [
            {
              "Company": "Google",
              "Role": "Senior AI Engineer",
              "Duration": "5 years"
            },
            {
              "Company": "Facebook",
              "Role": "Principal AI Engineer",
              "Duration": "3 years"
            }
          ],
          "Skills": [
            "Python",
            "TensorFlow",
            "PyTorch",
            "Natural Language Processing",
            "Machine Learning"
          ],
          "Languages": [
            "English",
            "French",
            "Spanish"
          ],
          "Certifications": [
            {
              "Name": "Certified AI Professional",
              "Issuing Body": "AI Professional Association"
            },
            {
              "Name": "Advanced AI Course Certificate",
              "Issuing Body": "AI Institute"
            }
          ]
        }
      }
    }
    ```
    """
    from json_repair import repair_json
    response = repair_json(response0)
    assert response.startswith('{')

    response0 = """  Here is an example employee profile in JSON format, with keys that are less than 64 characters and made of only alphanumerics, underscores, or hyphens:
    ```json
    {
      "employee_id": 1234,
      "name": "John Doe",
      "email": "johndoe@example.com",
      "job_title": "Software Engineer",
      "department": "Engineering",
      "hire_date": "2020-01-01",
      "salary": 100000,
      "manager_id": 5678
    }
    ```
    In Markdown, you can display this JSON code block like this:
    ```json
    ```
    {
      "employee_id": 1234,
      "name": "John Doe",
      "email": "johndoe@example.com",
      "job_title": "Software Engineer",
      "department": "Engineering",
      "hire_date": "2020-01-01",
      "salary": 100000,
      "manager_id": 5678
    }
    ```
    This will display the JSON code block with proper formatting and highlighting.
    """
    # from json_repair import repair_json
    from src.utils import get_json, repair_json_by_type
    import json

    response = repair_json_by_type(response0)
    assert json.loads(response)['employee_id'] == 1234
    print(response)

    response = get_json(response0, json_schema_type='object')
    assert json.loads(response)['employee_id'] == 1234
    print(response)


@wrap_test_forked
def test_dedup():
    # Example usage:
    names_list = ['Alice', 'Bob', 'Alice', 'Charlie', 'Bob', 'Alice']
    assert deduplicate_names(names_list) == ['Alice', 'Bob', 'Alice_1', 'Charlie', 'Bob_1', 'Alice_2']


# Test cases
def test_handle_json_normal():
    normal_json = {
        "name": "Henry",
        "age": 35,
        "skills": ["AI", "Machine Learning", "Data Science"],
        "workhistory": [
            {"company": "TechCorp", "duration": "2015-2020", "position": "Senior AI Scientist"},
            {"company": "AI Solutions", "duration": "2010-2015", "position": "AI Scientist"}
        ]
    }
    assert handle_json(normal_json) == normal_json


def test_handle_json_schema():
    schema_json = {
        "name": {"type": "string", "value": "Henry"},
        "age": {"type": "integer", "value": 35},
        "skills": {"type": "array", "items": [
            {"type": "string", "value": "AI", "maxLength": 10},
            {"type": "string", "value": "Machine Learning", "maxLength": 10},
            {"type": "string", "value": "Data Science", "maxLength": 10}
        ], "minItems": 3},
        "workhistory": {"type": "array", "items": [
            {"type": "object", "properties": {
                "company": {"type": "string", "value": "TechCorp"},
                "duration": {"type": "string", "value": "2015-2020"},
                "position": {"type": "string", "value": "Senior AI Scientist"}
            }, "required": ["company", "position"]},
            {"type": "object", "properties": {
                "company": {"type": "string", "value": "AI Solutions"},
                "duration": {"type": "string", "value": "2010-2015"},
                "position": {"type": "string", "value": "AI Scientist"}
            }, "required": ["company", "position"]}
        ]}
    }
    expected_result = {
        "name": "Henry",
        "age": 35,
        "skills": ["AI", "Machine Learning", "Data Science"],
        "workhistory": [
            {"company": "TechCorp", "duration": "2015-2020", "position": "Senior AI Scientist"},
            {"company": "AI Solutions", "duration": "2010-2015", "position": "AI Scientist"}
        ]
    }
    assert handle_json(schema_json) == expected_result


def test_handle_json_mixed():
    mixed_json = {
        "name": "Henry",
        "age": {"type": "integer", "value": 35},
        "skills": ["AI", {"type": "string", "value": "Machine Learning"}, "Data Science"],
        "workhistory": {"type": "array", "items": [
            {"type": "object", "properties": {
                "company": {"type": "string", "value": "TechCorp"},
                "duration": {"type": "string", "value": "2015-2020"},
                "position": {"type": "string", "value": "Senior AI Scientist"}
            }, "required": ["company", "position"]},
            {"company": "AI Solutions", "duration": "2010-2015", "position": "AI Scientist"}
        ]}
    }
    expected_result = {
        "name": "Henry",
        "age": 35,
        "skills": ["AI", "Machine Learning", "Data Science"],
        "workhistory": [
            {"company": "TechCorp", "duration": "2015-2020", "position": "Senior AI Scientist"},
            {"company": "AI Solutions", "duration": "2010-2015", "position": "AI Scientist"}
        ]
    }
    assert handle_json(mixed_json) == expected_result


def test_handle_json_empty():
    empty_json = {}
    assert handle_json(empty_json) == empty_json


def test_handle_json_no_schema():
    no_schema_json = {
        "name": {"first": "Henry", "last": "Smith"},
        "age": 35,
        "skills": ["AI", "Machine Learning", "Data Science"]
    }
    assert handle_json(no_schema_json) == no_schema_json


def test_json_repair_on_string():
    from json_repair import repair_json
    response0 = 'According to the information provided, the best safety assessment enum label is "Safe".'

    json_schema_type = 'object'
    response = get_json(response0, json_schema_type=json_schema_type)
    response = json.loads(response)
    assert isinstance(response, dict) and not response

    response = repair_json(response0)
    assert isinstance(response, str) and response in ['""', """''""", '', None]


# Example usage converted to pytest test cases
def test_check_input_type():
    # Valid URL
    assert check_input_type("https://example.com") == 'url'

    # Valid file path (Note: Adjust the path to match an actual file on your system for the test to pass)
    assert check_input_type("tests/receipt.jpg") == 'file'

    # Valid base64 encoded image
    assert check_input_type("b'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA...") == 'base64'

    # Non-string inputs
    assert check_input_type(b"bytes data") == 'unknown'
    assert check_input_type(12345) == 'unknown'
    assert check_input_type(["list", "of", "strings"]) == 'unknown'

    # Invalid URL
    assert check_input_type("invalid://example.com") == 'unknown'

    # Invalid file path
    assert check_input_type("/path/to/invalid/file.txt") == 'unknown'

    # Plain string
    assert check_input_type("just a string") == 'unknown'


def test_process_file_list():
    # Create a list of test files
    test_files = [
        "tests/videotest.mp4",
        "tests/dental.png",
        "tests/fastfood.jpg",
        "tests/ocr2.png",
        "tests/receipt.jpg",
        "tests/revenue.png",
        "tests/jon.png",
        "tests/ocr1.png",
        "tests/ocr3.png",
        "tests/screenshot.png",
    ]

    output_dir = os.path.join(tempfile.gettempdir(), 'image_path_%s' % str(uuid.uuid4()))
    print(output_dir, file=sys.stderr)

    # Process the files
    processed_files = process_file_list(test_files, output_dir, resolution=(640, 480), image_format="jpg", verbose=True)

    # Print the resulting list of image files
    print("Processed files:")
    for file in processed_files:
        print(file, file=sys.stderr)
        assert os.path.isfile(file)
    assert len(processed_files) == len(
        test_files) - 1 + 17 + 4  # 17 is the number of images generated from the video file


def test_process_file_list_extract_frames():
    # Create a list of test files
    test_files = [
        "tests/videotest.mp4",
        "tests/dental.png",
        "tests/fastfood.jpg",
        "tests/ocr2.png",
        "tests/receipt.jpg",
        "tests/revenue.png",
        "tests/jon.png",
        "tests/ocr1.png",
        "tests/ocr3.png",
        "tests/screenshot.png",
    ]

    output_dir = os.path.join(tempfile.gettempdir(), 'image_path_%s' % str(uuid.uuid4()))
    print(output_dir, file=sys.stderr)

    # Process the files
    processed_files = process_file_list(test_files, output_dir, resolution=(640, 480), image_format="jpg",
                                        video_frame_period=0, extract_frames=10, verbose=True)

    # Print the resulting list of image files
    print("Processed files:")
    for file in processed_files:
        print(file, file=sys.stderr)
        assert os.path.isfile(file)
    assert len(processed_files) == len(test_files) - 1 + 10  # 10 is the number of images generated from the video file


def test_process_youtube():
    # Create a list of test files
    test_files = [
        "https://www.youtube.com/shorts/fRkZCriQQNU",
        "tests/screenshot.png"
    ]

    output_dir = os.path.join(tempfile.gettempdir(), 'image_path_%s' % str(uuid.uuid4()))
    print(output_dir, file=sys.stderr)

    # Process the files
    processed_files = process_file_list(test_files, output_dir, resolution=(640, 480), image_format="jpg",
                                        video_frame_period=0, extract_frames=10, verbose=True)

    # Print the resulting list of image files
    print("Processed files:")
    for file in processed_files:
        print(file, file=sys.stderr)
        assert os.path.isfile(file)
    assert len(processed_files) == len(test_files) - 1 + 10  # 10 is the number of images generated from the video file


def test_process_animated_gif():
    # Create a list of test files
    test_files = [
        "tests/test_animated_gif.gif",
        "tests/screenshot.png",
    ]

    output_dir = os.path.join(tempfile.gettempdir(), 'image_path_%s' % str(uuid.uuid4()))
    print(output_dir, file=sys.stderr)

    # Process the files
    processed_files = process_file_list(test_files, output_dir, resolution=(640, 480), image_format="jpg",
                                        video_frame_period=0, extract_frames=10, verbose=True)

    # Print the resulting list of image files
    print("Processed files:")
    for file in processed_files:
        print(file, file=sys.stderr)
        assert os.path.isfile(file)
    assert len(processed_files) == len(test_files) - 1 + 3  # 3 is the number of images generated from the animated gif


def test_process_animated_gif2():
    # Create a list of test files
    test_files = [
        "tests/test_animated_gif.gif",
        "tests/screenshot.png"
    ]

    output_dir = os.path.join(tempfile.gettempdir(), 'image_path_%s' % str(uuid.uuid4()))
    print(output_dir, file=sys.stderr)

    # Process the files
    processed_files = process_file_list(test_files, output_dir, verbose=True)

    # Print the resulting list of image files
    print("Processed files:")
    for file in processed_files:
        print(file, file=sys.stderr)
        assert os.path.isfile(file)
    assert len(processed_files) == len(test_files) - 1 + 3  # 3 is the number of images generated from the animated gif


def test_process_animated_gif3():
    # Create a list of test files
    test_files = [
        "tests/test_animated_gif.gif",
        "tests/screenshot.png"
    ]

    output_dir = os.path.join(tempfile.gettempdir(), 'image_path_%s' % str(uuid.uuid4()))
    print(output_dir, file=sys.stderr)

    # Process the files
    processed_files = process_file_list(test_files, output_dir, video_frame_period=1, verbose=True)

    # Print the resulting list of image files
    print("Processed files:")
    for file in processed_files:
        print(file, file=sys.stderr)
        assert os.path.isfile(file)
    assert len(processed_files) == len(
        test_files) - 1 + 60  # 60 is the number of images generated from the animated gif


def test_process_mixed():
    # Create a list of test files
    test_files = [
        "tests/videotest.mp4",
        "https://www.youtube.com/shorts/fRkZCriQQNU",
        "tests/screenshot.png",
        "tests/test_animated_gif.gif",
    ]

    output_dir = os.path.join(tempfile.gettempdir(), 'image_path_%s' % str(uuid.uuid4()))
    print(output_dir, file=sys.stderr)

    # Process the files
    processed_files = process_file_list(test_files, output_dir, resolution=(640, 480), image_format="jpg",
                                        video_frame_period=0, extract_frames=10, verbose=True)

    # Print the resulting list of image files
    print("Processed files:")
    for file in processed_files:
        print(file, file=sys.stderr)
        assert os.path.isfile(file)
    assert len(processed_files) == len(test_files) - 1 + 29  # 28 is the number of images generated from the video files


def test_update_db():
    auth_filename = "test.db"
    remove(auth_filename)
    from src.db_utils import fetch_user
    assert fetch_user(auth_filename, '', verbose=True) == {}

    username = "jon"
    updates = {
        "selection_docs_state": {
            "langchain_modes": ["NewMode1"],
            "langchain_mode_paths": {"NewMode1": "new_mode_path1"},
            "langchain_mode_types": {"NewMode1": "shared"}
        }
    }
    from src.db_utils import append_to_user_data
    append_to_user_data(auth_filename, username, updates, verbose=True)

    auth_dict = fetch_user(auth_filename, username, verbose=True)

    assert auth_dict == {'jon': {'selection_docs_state': {'langchain_mode_paths': {'NewMode1': 'new_mode_path1'},
                                                          'langchain_mode_types': {'NewMode1': 'shared'},
                                                          'langchain_modes': ['NewMode1']}}}

    updates = {
        "selection_docs_state": {
            "langchain_modes": ["NewMode"],
            "langchain_mode_paths": {"NewMode": "new_mode_path"},
            "langchain_mode_types": {"NewMode": "shared"}
        }
    }
    from src.db_utils import append_to_users_data
    append_to_users_data(auth_filename, updates, verbose=True)

    auth_dict = fetch_user(auth_filename, username, verbose=True)
    assert auth_dict == {'jon': {'selection_docs_state':
                                     {'langchain_mode_paths': {'NewMode1': 'new_mode_path1',
                                                               "NewMode": "new_mode_path"},
                                      'langchain_mode_types': {'NewMode1': 'shared', "NewMode": "shared"},
                                      'langchain_modes': ['NewMode1', 'NewMode']}}}


def test_encode_chat_template():
    jinja_template = """
{{ bos_token }}
{%- if messages[0]['role'] == 'system' -%}
    {% set system_message = messages[0]['content'].strip() %}
    {% set loop_messages = messages[1:] %}
{%- else -%}
    {% set system_message = 'This is a chat between a user and an artificial intelligence assistant. The assistant gives helpful, detailed, and polite answers to the user\'s questions based on the context. The assistant should also indicate when the answer cannot be found in the context.' %}
    {% set loop_messages = messages %}
{%- endif -%}

System: {{ system_message }}

{% for message in loop_messages %}
    {%- if message['role'] == 'user' -%}
        User: {{ message['content'].strip() + '\n' }}
    {%- else -%}
        Assistant: {{ message['content'].strip() + '\n' }}
    {%- endif %}
    {% if loop.last and message['role'] == 'user' %}
        Assistant:
    {% endif %}
{% endfor %}
"""

    encoded_template = base64_encode_jinja_template(jinja_template)
    print("\nEncoded Template:", encoded_template)

    model_lock_option = f"""--model_lock="[{{'inference_server': 'vllm_chat:149.130.210.116', 'base_model': 'nvidia/Llama3-ChatQA-1.5-70B', 'visible_models': 'nvidia/Llama3-ChatQA-1.5-70B', 'h2ogpt_key': '62224bfb-c832-4452-81e7-8a4bdabbe164', 'chat_template': '{encoded_template}'}}]"
"""

    print("Command-Line Option:")
    print(model_lock_option)

    # Example of decoding back from the command-line option
    command_line_option = model_lock_option.strip('--model_lock=')
    # double ast.literal_eval due to quoted quote for model_lock_option
    parsed_model_lock_option = ast.literal_eval(ast.literal_eval(command_line_option))

    encoded_template_from_option = parsed_model_lock_option[0]['chat_template']
    decoded_template = base64_decode_jinja_template(encoded_template_from_option)

    print("Decoded Template:")
    print(decoded_template)

    assert jinja_template == decoded_template


def test_depth():
    example_list = [[['Dog', ['/tmp/gradio/image_Dog_d2b19221_6f70_4987_bda8_09be952eae93.png']],
                     ['Who are you?', ['/tmp/gradio/image_Wh_480bd8318d01b570b61e77a9306aef87_c41f.png']],
                     ['Who ar eyou?',
                      "I apologize for the confusion earlier!\n\nI am LLaMA, an AI assistant developed by Meta AI that can understand and respond to human input in a conversational manner. I'm not a human, but a computer program designed to simulate conversation, answer questions, and even generate text based on the input I receive.\n\nI can assist with a wide range of topics, from general knowledge to entertainment, and even create stories or dialogues. I'm constantly learning and improving my responses based on the interactions I have with users like you.\n\nSo, feel free to ask me anything, and I'll do my best to help!"]],
                    [], [], [], [], [], [], [], [], [], [], []]
    assert get_gradio_depth(example_list) == 3

    example_list = [[[['Dog'], ['/tmp/gradio/image_Dog_d2b19221_6f70_4987_bda8_09be952eae93.png']],
                     ['Who are you?', ['/tmp/gradio/image_Wh_480bd8318d01b570b61e77a9306aef87_c41f.png']],
                     ['Who ar eyou?',
                      "I apologize for the confusion earlier!\n\nI am LLaMA, an AI assistant developed by Meta AI that can understand and respond to human input in a conversational manner. I'm not a human, but a computer program designed to simulate conversation, answer questions, and even generate text based on the input I receive.\n\nI can assist with a wide range of topics, from general knowledge to entertainment, and even create stories or dialogues. I'm constantly learning and improving my responses based on the interactions I have with users like you.\n\nSo, feel free to ask me anything, and I'll do my best to help!"]],
                    [], [], [], [], [], [], [], [], [], [], []]
    assert get_gradio_depth(example_list) == 3

    example_list = [[['Dog', "Bad Dog"], ['Who are you?', "Image"], ['Who ar eyou?',
                                                                     "I apologize for the confusion earlier!\n\nI am LLaMA, an AI assistant developed by Meta AI that can understand and respond to human input in a conversational manner. I'm not a human, but a computer program designed to simulate conversation, answer questions, and even generate text based on the input I receive.\n\nI can assist with a wide range of topics, from general knowledge to entertainment, and even create stories or dialogues. I'm constantly learning and improving my responses based on the interactions I have with users like you.\n\nSo, feel free to ask me anything, and I'll do my best to help!"]],
                    [], [], [], [], [], [], [], [], [], [], []]
    assert get_gradio_depth(example_list) == 3

    example_list = [[[['Dog', "Bad Dog"], ['Who are you?', "Image"], ['Who ar eyou?',
                                                                      "I apologize for the confusion earlier!\n\nI am LLaMA, an AI assistant developed by Meta AI that can understand and respond to human input in a conversational manner. I'm not a human, but a computer program designed to simulate conversation, answer questions, and even generate text based on the input I receive.\n\nI can assist with a wide range of topics, from general knowledge to entertainment, and even create stories or dialogues. I'm constantly learning and improving my responses based on the interactions I have with users like you.\n\nSo, feel free to ask me anything, and I'll do my best to help!"]],
                     [], [], [], [], [], [], [], [], [], [], []]]
    assert get_gradio_depth(example_list) == 4

    example_list = [['Dog', "Bad Dog"], ['Who are you?', "Image"]]
    assert get_gradio_depth(example_list) == 2

    # more cases
    example_list = []
    assert get_gradio_depth(example_list) == 0

    example_list = [1, 2, 3]
    assert get_gradio_depth(example_list) == 1

    example_list = [[1], [2], [3]]
    assert get_gradio_depth(example_list) == 1

    example_list = [[[1]], [[2]], [[3]]]
    assert get_gradio_depth(example_list) == 2

    example_list = [[[[1]]], [[[2]]], [[[3]]]]
    assert get_gradio_depth(example_list) == 3

    example_list = [[[[[1]]]], [[[[2]]]], [[[[3]]]]]
    assert get_gradio_depth(example_list) == 4

    example_list = [[], [1], [2, [3]], [[[4]]]]
    assert get_gradio_depth(example_list) == 3

    example_list = [[], [[[[1]]]], [2, [3]], [[[4]]]]
    assert get_gradio_depth(example_list) == 4

    example_list = [[], [[[[[1]]]]], [2, [3]], [[[4]]]]
    assert get_gradio_depth(example_list) == 5

    example_list = [[[[[1]]]], [[[[2]]]], [[[3]]], [[4]], [5]]
    assert get_gradio_depth(example_list) == 4

    example_list = [[[[[1]]]], [[[[2]]]], [[[3]]], [[4]], [5], []]
    assert get_gradio_depth(example_list) == 4


def test_schema_to_typed():
    TEST_SCHEMA = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
            "skills": {
                "type": "array",
                "items": {"type": "string", "maxLength": 10},
                "minItems": 3
            },
            "work history": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "company": {"type": "string"},
                        "duration": {"type": "string"},
                        "position": {"type": "string"}
                    },
                    "required": ["company", "position"]
                }
            }
        },
        "required": ["name", "age", "skills", "work history"]
    }

    Schema = create_typed_dict(TEST_SCHEMA)

    # Example usage of the generated TypedDict
    person: Schema = {
        "name": "John Doe",
        "age": 30,
        "skills": ["Python", "TypeScript", "Docker"],
        "work history": [
            {"company": "TechCorp", "position": "Developer", "duration": "2 years"},
            {"company": "DataInc", "position": "Data Scientist"}
        ]
    }

    print(person)


def test_genai_schema():
    # Usage example
    TEST_SCHEMA = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
            "skills": {
                "type": "array",
                "items": {"type": "string", "maxLength": 10},
                "minItems": 3
            },
            "work history": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "company": {"type": "string"},
                        "duration": {"type": "string"},
                        "position": {"type": "string"}
                    },
                    "required": ["company", "position"]
                }
            },
            "status": {
                "type": "string",
                "enum": ["active", "inactive", "on leave"]
            }
        },
        "required": ["name", "age", "skills", "work history", "status"]
    }

    from src.utils_langchain import convert_to_genai_schema
    genai_schema = convert_to_genai_schema(TEST_SCHEMA)

    # Print the schema (this will show the structure, but not all details)
    print(genai_schema)

    # You can now use this schema with the Gemini API
    # For example:
    # response = model.generate_content(prompt, response_schema=genai_schema)


def test_genai_schema_more():
    # Test cases
    TEST_SCHEMAS = [
        # Object schema
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The person's name"},
                "age": {"type": "integer", "description": "The person's age"},
                "height": {"type": "number", "format": "float", "description": "Height in meters"},
                "is_student": {"type": "boolean", "description": "Whether the person is a student"},
                "skills": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of skills"
                },
                "address": {
                    "type": "object",
                    "properties": {
                        "street": {"type": "string"},
                        "city": {"type": "string"},
                        "country": {"type": "string"}
                    },
                    "required": ["street", "city"],
                    "description": "Address details"
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "inactive", "on leave"],
                    "description": "Current status"
                }
            },
            "required": ["name", "age", "is_student"],
            "description": "A person's profile"
        },
        # Array schema
        {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"}
                },
                "required": ["id"]
            },
            "description": "List of items"
        },
        # String schema
        {
            "type": "string",
            "format": "email",
            "description": "Email address"
        },
        # Number schema
        {
            "type": "number",
            "format": "double",
            "description": "A floating-point number"
        },
        # Boolean schema
        {
            "type": "boolean",
            "description": "A true/false value"
        }
    ]

    from src.utils_langchain import convert_to_genai_schema

    # Test the conversion
    for i, schema in enumerate(TEST_SCHEMAS, 1):
        print(f"\nTest Schema {i}:")
        genai_schema = convert_to_genai_schema(schema)
        print(genai_schema)


def test_pymupdf4llm():
    from langchain_community.document_loaders import PyMuPDFLoader
    from src.utils_langchain import PyMuPDF4LLMLoader

    times_pymupdf = []
    times_pymupdf4llm = []
    files = [os.path.join('tests', x) for x in os.listdir('tests')]
    files += [os.path.join('/home/jon/Downloads/', x) for x in os.listdir('/home/jon/Downloads/')]
    files = ['/home/jon/Downloads/Tabasco_Ingredients_Products_Guide.pdf']
    for file in files:
        if not file.endswith('.pdf'):
            continue

        t0 = time.time()
        doc = PyMuPDFLoader(file).load()
        assert doc is not None
        print('pymupdf: %s %s %s' % (file, len(doc), time.time() - t0))
        times_pymupdf.append((time.time() - t0)/len(doc))
        for page in doc:
            print(page)

        t0 = time.time()
        doc = PyMuPDF4LLMLoader(file).load()
        assert doc is not None
        print('pymupdf4llm: %s %s %s' % (file, len(doc), time.time() - t0))
        times_pymupdf4llm.append((time.time() - t0)/len(doc))
        for page in doc:
            print(page)

        if len(times_pymupdf) > 30:
            break

    print("pymupdf stats:")
    compute_stats(times_pymupdf)

    print("pymupdf4llm stats:")
    compute_stats(times_pymupdf4llm)


def compute_stats(times_in_seconds):

    # Compute statistics
    min_time = min(times_in_seconds)
    max_time = max(times_in_seconds)
    average_time = sum(times_in_seconds) / len(times_in_seconds)

    # Print the results
    print(f"Min time: {min_time} seconds")
    print(f"Max time: {max_time} seconds")
    print(f"Average time: {average_time} seconds")
