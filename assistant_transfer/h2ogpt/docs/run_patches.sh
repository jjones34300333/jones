#!/bin/bash
set -o pipefail
set -ex

#
#* Deal with not-thread-safe things in LangChain:
#
sp=`python3.10 -c 'import site; print(site.getsitepackages()[0])'`
sed -i  's/with HiddenPrints():/if True:/g' $sp/langchain_community/utilities/serpapi.py
#sed -i 's/"progress": Status.PROGRESS,/"progress": Status.PROGRESS,\n            "heartbeat": Status.PROGRESS,/g' gradio_client/utils.py
#sed -i 's/async for line in response.aiter_text():/async for line in response.aiter_lines():\n                if len(line) == 0:\n                    continue\n                if line == """{"detail":"Not Found"}""":\n                    continue/g' gradio_client/utils.py

# aggressively remove thread-unsafe reassignment of stderr stdout
# WIP
# find "$sp" -type f -name "*.py" -exec sed -i -E 's/(sys\.stdout\s*=\s*.*)/pass # \1/; s/(sys\.stderr\s*=\s*.*)/pass # \1/' {} +

# use pytubefix instead, pytube too old and various issues
#sed -i 's/Pytube/PytubeFix/g'  $sp/fiftyone/utils/youtube.py
#sed -i 's/pytube>=15/pytube>=6/g' $sp/fiftyone/utils/youtube.py
#sed -i 's/pytube/pytubefix/g' $sp/fiftyone/utils/youtube.py

# diff -Naru /home/jon/miniconda3/envs/h2ogpt/lib/python3.10/site-packages/pytubefix/extract.py ~/extract.py > docs/pytubefix.patch
#patch $sp/pytubefix/extract.py docs/pytubefix.patch

# fix asyncio same way websockets was fixed, else keep hitting errors in async calls
# https://github.com/python-websockets/websockets/commit/f9fd2cebcd42633ed917cd64e805bea17879c2d7
sed -i "s/except OSError:/except (OSError, RuntimeError):/g" $sp/anyio/_backends/_asyncio.py

# https://github.com/gradio-app/gradio/issues/7086
sed -i 's/while True:/while True:\n            time.sleep(0.001)\n/g' $sp/gradio_client/client.py

# diff -Naru $sp/transformers/modeling_utils.py modeling_utils.py > docs/trans.patch
patch $sp/transformers/modeling_utils.py docs/trans.patch

# diff -Naru /home/jon/miniconda3/envs/h2ogpt/lib/python3.10/site-packages/TTS/tts/layers/xtts/stream_generator.py new.py > docs/xtt.patch
patch $sp/TTS/tts/layers/xtts/stream_generator.py docs/xtt.patch

# diff -Naru /home/jon/miniconda3/envs/h2ogpt/lib/python3.10/site-packages/transformers/generation/utils.py ~/utils.py  > docs/trans2.patch
patch $sp/transformers/generation/utils.py docs/trans2.patch

# diff -Naru /home/jon/miniconda3/envs/h2ogpt/lib/python3.10/site-packages/langchain_google_genai/chat_models.py ~/chat_models.py > docs/google.patch
patch $sp/langchain_google_genai/chat_models.py docs/google.patch

# diff -Naru /home/jon/miniconda3/envs/h2ogpt/lib/python3.10/site-packages/autogen/token_count_utils.py ~/token_count_utils.py > docs/autogen.patch
patch $sp/autogen/token_count_utils.py docs/autogen.patch

# diff -Naru /home/jon/miniconda3/envs/h2ogpt/lib/python3.10/site-packages/autogen/agentchat/conversable_agent.py ~/conversable_agent.py > docs/autogen2.patch
patch $sp/autogen/agentchat/conversable_agent.py docs/autogen2.patch

# diff -Naru /home/jon/miniconda3/envs/h2ogpt/lib/python3.10/site-packages/openai/_streaming.py ~/_streaming.py > docs/openai.patch
patch $sp/openai/_streaming.py docs/openai.patch

find $sp/flaml/ -type f -name '*.py' -exec sed -i 's/^except ImportError:/except (ModuleNotFoundError, ImportError):/g' {} +
