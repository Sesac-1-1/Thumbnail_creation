"""Run from the repository root: streamlit run app.py."""
from uuid import uuid4

import streamlit as st

from services import file_manager as fm
from services.resource_service import ResourceServiceError
from services.video_service import VideoServiceError
import workflow

STALE_OUTPUT_AGE_SECONDS = 24 * 60 * 60


def _upload_changed() -> None:
    st.session_state['_upload_revision'] = uuid4().hex


def _message(error: Exception) -> str:
    if isinstance(error, VideoServiceError):
        return 'Unable to read this video format or selected frame. Try another video.'
    if isinstance(error, ResourceServiceError):
        return 'System resource measurement is currently unavailable.'
    if isinstance(error, OSError):
        return 'Unable to access the upload or output files. Check available disk space and permissions.'
    return 'The video state or selected options are invalid. Check the options or upload the video again.'


def _details(values: dict[str, str]) -> None:
    st.table({'Field': list(values), 'Value': list(values.values())})


def main() -> None:
    st.set_page_config(page_title='Video Thumbnail Generator', layout='wide')
    st.title('Resource-Aware Video Thumbnail Generator')
    st.caption('Preview candidates, select one frame, and download a JPEG or PNG thumbnail.')
    state = st.session_state
    if not state.get('_startup_cleanup_done'):
        try:
            fm.cleanup_stale_thumbnails(STALE_OUTPUT_AGE_SECONDS)
        except (OSError, ValueError) as error:
            st.warning(_message(error))
        state['_startup_cleanup_done'] = True
    upload = st.file_uploader('Upload video', type=['mp4', 'm4v', 'mov', 'avi', 'mkv', 'webm', 'mpeg', 'mpg'],
                              key='_upload', on_change=_upload_changed)
    if upload is None:
        if state.get('job_id') is not None:
            try:
                workflow.reset_session(state)
            except (OSError, ValueError) as error:
                st.error(_message(error))
        st.info('Upload a video to generate candidate previews.')
        return
    if '_upload_revision' not in state:
        _upload_changed()
    try:
        workflow.prepare_upload(state, upload, upload.name, state['_upload_revision'])
    except (VideoServiceError, ValueError, OSError) as error:
        st.error(_message(error))
        return
    if state.get('_processing_error'):
        st.error(state['_processing_error'])
        if st.button('Retry processing'):
            state.pop('_processing_error')
            st.rerun()
        return
    if not state.get('processing_result'):
        try:
            with st.spinner('Sampling frames and generating small previews...'):
                workflow.process_candidates(state)
        except (VideoServiceError, ResourceServiceError, ValueError, OSError) as error:
            state['_processing_error'] = _message(error)
            st.error(state['_processing_error'])
            return
    info = state['video_metadata']
    st.subheader('Video information')
    _details({
        'Filename': state['source_filename'],
        'File size': f"{info['file_size_mb']:.2f} MiB",
        'Duration': f"{info['duration_seconds']:.2f} seconds" if info['frame_count'] else 'Unknown',
        'Resolution': info['resolution'], 'FPS': f"{info['fps']:.3f}",
        'Total frames': str(info['frame_count']) if info['frame_count'] else 'Unknown',
    })
    report = state['resource_metrics']
    st.subheader('Processing report')
    if not report['resource_available']:
        st.warning(report['resource_error'])
    if not info['frame_count']:
        st.info('Frame count is unknown. Previews cover a bounded sequence from the beginning; the sampling ratio is unavailable.')
    def metric(name: str, unit: str) -> str:
        value = report.get(name)
        return 'Unavailable' if value is None else f'{value:.2f}{unit}'
    _details({
        'System CPU before': metric('system_cpu_before', '%'),
        'System CPU after': metric('system_cpu_after', '%'),
        'System RAM before': metric('system_memory_percent_before', '%'),
        'Process memory before': metric('process_memory_before_mb', ' MiB'),
        'Process memory after': metric('process_memory_after_mb', ' MiB'),
        'Process memory delta': metric('process_memory_delta_mb', ' MiB'),
        'Requested samples': str(state['requested_sample_count']),
        'Successfully processed': str(state['actual_sample_count']),
        'Sampling ratio': 'Unknown' if report['sampling_ratio'] is None else f"{report['sampling_ratio']:.2%}",
        'Processing time': metric('elapsed_seconds', ' seconds'),
    })
    st.caption('Time covers sampling policy, candidate decoding and preview encoding. RSS is process-wide, not a per-session peak.')
    previews = state['candidate_previews']
    st.subheader('Candidate previews')
    columns = st.columns(4)
    for position, item in enumerate(previews):
        columns[position % 4].image(item['preview'], width=320,
                                    caption=f"Frame {item['index']} · {item['timestamp_seconds']:.2f}s")
    labels = {item['index']: f"Frame {item['index']} · {item['timestamp_seconds']:.2f}s" for item in previews}
    selected = st.radio('Select a candidate', options=state['candidate_positions'],
                        format_func=labels.get, key='selected_frame_index', horizontal=True)
    image_format = st.selectbox('Format', ['JPEG', 'PNG'])
    width = st.number_input('Width', min_value=16, max_value=3840, value=320, step=1)
    height = st.number_input('Height', min_value=16, max_value=2160, value=180, step=1)
    crop = st.checkbox('Center crop', value=False)
    try:
        saved = workflow.generate_selected_thumbnail(state, selected, image_format, (width, height), crop)
        st.subheader('Final thumbnail')
        st.image(str(saved), width=min(width, 640))
        with saved.open('rb') as output:
            st.download_button('Download thumbnail', output, file_name=saved.name,
                               mime='image/jpeg' if image_format == 'JPEG' else 'image/png')
    except (VideoServiceError, ValueError, OSError) as error:
        st.error(_message(error))


if __name__ == '__main__':
    main()
