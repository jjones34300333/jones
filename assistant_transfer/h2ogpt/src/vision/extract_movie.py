import os
import sys
import uuid

from src.utils import makedirs, sanitize_filename, get_gradio_tmp


def extract_unique_frames(urls=None, file=None, download_dir=None, export_dir=None, extract_frames=10):
    temp_workaround = False
    if temp_workaround:
        download_dir = './'
    else:
        download_dir = download_dir or os.getenv('VID_DOWNLOADS', "viddownloads")
        download_dir = os.path.join(download_dir, str(uuid.uuid4()))
        makedirs(download_dir, exist_ok=True)
    # os.environ['FIFTYONE_DISABLE_SERVICES'] = 'True'
    if urls:
        if 'openai_server' not in sys.path:
            sys.path.append('openai_server')
        from openai_server.agent_tools.download_web_video import download_web_video
        for url in urls:
            download_web_video(video_url=url, base_url="https://www.youtube.com", output_dir=download_dir)
        #import fiftyone.utils.youtube as fouy
        #fouy.download_youtube_videos(urls, download_dir=download_dir)

    # Create a FiftyOne Dataset
    import fiftyone as fo
    if file:
        dataset = fo.Dataset.from_videos([file])
    else:
        dataset = fo.Dataset.from_videos_dir(download_dir)

    # Convert videos to images, sample 1 frame per second
    frame_view = dataset.to_frames(sample_frames=True, fps=1)

    import fiftyone.brain as fob

    # Index images by similarity
    results = fob.compute_similarity(frame_view, brain_key="frame_sim")

    # Find maximally unique frames
    num_unique = min(extract_frames, frame_view.count())  # Scale this to whatever you want
    results.find_unique(num_unique)
    unique_view = frame_view.select(results.unique_ids)

    # Visualize in the App
    # session = fo.launch_app(frame_view)
    # session = fo.launch_app(unique_view)

    san_file = sanitize_filename(os.path.basename(file)) if file else None

    gradio_tmp = get_gradio_tmp()
    if san_file:
        export_dir = export_dir or os.path.join(gradio_tmp, "extraction_%s" % san_file)
        if os.path.isdir(export_dir):
            export_dir += "_%s" % str(uuid.uuid4())
    else:
        export_dir = export_dir or os.path.join(gradio_tmp, "extraction_%s" % str(uuid.uuid4()))
    makedirs(export_dir, exist_ok=True)
    unique_view.export(export_dir, dataset_type=fo.types.VideoDirectory)
    return export_dir
