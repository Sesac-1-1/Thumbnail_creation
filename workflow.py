"""Streamlit-independent orchestration. State belongs to the caller's session.

No images/arrays, open captures or unfinished timers persist in session state.
Only encoded candidate previews, metadata, finished metrics and file paths do.
"""
from collections.abc import MutableMapping
from contextlib import closing
from pathlib import Path
import time
from typing import Any, BinaryIO

from services import file_manager as fm
from services import resource_service as rs
from services import thumbnail_service as ts
from services import video_service as vs

SESSION_KEYS = (
    'job_id', 'video_id', 'video_path', 'upload_id', 'source_filename',
    'video_metadata', 'candidate_frames', 'candidate_previews', 'candidate_positions',
    'selected_frame', 'selected_frame_index', 'requested_sample_count',
    'actual_sample_count', 'processing_result', 'resource_metrics',
    'saved_thumbnail_path', '_result_options', '_processing_error',
)


def reset_session(state: MutableMapping[str, Any]) -> None:
    """Release only this session's files, then clear video-dependent keys.

    Cleanup errors propagate and retain the references for an idempotent retry.
    """
    if state.get('saved_thumbnail_path') is not None:
        fm.delete_thumbnail(state['saved_thumbnail_path'])
    if state.get('job_id') is not None:
        fm.cleanup_temp_files(fm.TEMP_DIR / state['job_id'])
    for key in SESSION_KEYS:
        state.pop(key, None)


def prepare_upload(
    state: MutableMapping[str, Any], upload: BinaryIO, filename: str, upload_id: str,
) -> bool:
    """Save once per uploader change token; return True for a new upload.

    The UI creates a token in its uploader on_change callback, not on every
    rerun. File identity is checked separately against the job-local video.
    """
    if not isinstance(upload_id, str) or not upload_id:
        raise ValueError('upload_id must be a nonempty change token')
    if state.get('upload_id') == upload_id:
        if state.get('video_id') != vs.get_video_id(state['video_path']):
            raise ValueError('Uploaded video changed; upload it again')
        return False
    reset_session(state)
    job = fm.create_temp_job_dir()
    try:
        path = fm.save_uploaded_video(job, upload, Path(filename).suffix.lower())
        video_id = vs.get_video_id(path)
    except (OSError, ValueError, vs.VideoServiceError):
        fm.cleanup_temp_files(job)
        raise
    state.update(job_id=job.name, video_id=video_id, video_path=path,
                 upload_id=upload_id, source_filename=Path(filename).name)
    return True


def process_candidates(state: MutableMapping[str, Any]) -> None:
    """Process once per video; timing covers policy, decoding and preview encoding.

    Before/after resource snapshot delays, metadata I/O, user selection and
    downloads are excluded. No unfinished measurement is saved in state.
    """
    path = state['video_path']
    video_id = vs.get_video_id(path)
    if state['video_id'] != video_id:
        raise ValueError('Uploaded video identity changed')
    info = state.get('video_metadata')
    if info is not None and info.get('video_id') != video_id:
        raise ValueError('Cached video metadata belongs to another upload')
    if state.get('processing_result') == video_id:
        return
    info = info if info is not None else vs.get_video_info(path)
    resource_error = None
    try:
        measurement = rs.start_measurement()
    except rs.ResourceServiceError:
        measurement = None
        resource_error = 'Resource measurement unavailable; using conservative sampling.'
    requested = (rs.choose_sample_count(measurement['resources']['cpu_percent'],
                                       measurement['resources']['memory_percent'])
                 if measurement is not None else rs.FALLBACK_SAMPLE_COUNT)
    started_at = measurement['started_at'] if measurement is not None else time.perf_counter()
    previews = []
    with closing(vs.iter_frames(path, requested, info)) as frames:
        for item in frames:
            preview = ts.create_preview(item['frame'])
            previews.append({'index': item['index'],
                             'timestamp_seconds': item['timestamp_seconds'], 'preview': preview})
            del item  # Release consumer reference before the iterator reads again.
    elapsed = time.perf_counter() - started_at
    counts = dict(requested_sample_count=requested, actual_sample_count=len(previews),
                  total_frames=info['frame_count'])
    report = None
    if measurement is not None:
        try:
            report = rs.finish_measurement(measurement, **counts)
        except rs.ResourceServiceError:
            resource_error = 'Final resource measurement unavailable; previews are still usable.'
    if report is None:
        report = dict(counts, frames_processed=len(previews), elapsed_seconds=elapsed,
                      sampling_ratio=len(previews) / info['frame_count'] if info['frame_count'] else None)
        if measurement is not None:
            before = measurement['resources']
            report.update(system_cpu_before=before['cpu_percent'],
                          system_memory_percent_before=before['memory_percent'],
                          process_memory_before_mb=before['process_memory_mb'])
    report.update(resource_available=resource_error is None, resource_error=resource_error)
    state.update(video_metadata=info, candidate_previews=previews,
                 candidate_positions=[item['index'] for item in previews],
                 requested_sample_count=requested, actual_sample_count=len(previews),
                 resource_metrics=report, processing_result=video_id)


def generate_selected_thumbnail(
    state: MutableMapping[str, Any], index: int, image_format: str = 'JPEG',
    size: tuple[int, int] = (320, 180), crop: bool = False,
) -> Path:
    """Re-read one selected frame; same options reuse the existing saved result."""
    if type(index) is not int or index not in state.get('candidate_positions', []):
        raise ValueError('Select an existing candidate frame')
    if state['video_id'] != vs.get_video_id(state['video_path']):
        raise ValueError('Uploaded video identity changed')
    if state['video_metadata'].get('video_id') != state['video_id']:
        raise ValueError('Cached video metadata belongs to another upload')
    options = (state['video_id'], index, image_format, size, crop)
    old = state.get('saved_thumbnail_path')
    if old is not None and old.exists() and state.get('_result_options') == options:
        return old
    frame = vs.extract_frame_at_index(state['video_path'], index, state['video_metadata'])
    thumbnail = ts.create_thumbnail(frame, size, crop)
    del frame
    with thumbnail:
        saved = ts.save_thumbnail(thumbnail, image_format)
    try:
        if old is not None:
            fm.delete_thumbnail(old)
    except (OSError, ValueError):
        fm.delete_thumbnail(saved)
        raise
    if state.get('selected_frame_index') != index:
        state['selected_frame_index'] = index
    state.update(saved_thumbnail_path=saved, _result_options=options)
    return saved
