import streamlit as st
import yt_dlp
import re
from datetime import datetime
import os
import json
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def create_retry_session(retries=3, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504)):
    """Create a requests session with automatic retry logic."""
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)
    return session


def extract_video_id(url):
    """
    Extract video ID from various YouTube URL formats.

    Supports:
    - https://www.youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    - https://www.youtube.com/embed/VIDEO_ID
    - https://m.youtube.com/watch?v=VIDEO_ID
    """
    patterns = [
        r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([^&\n?#]+)',
        r'youtube\.com\/watch\?.*v=([^&\n?#]+)'
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    return None


def get_transcript(video_id):
    """
    Fetch transcript for a given video ID using yt-dlp with comprehensive error handling.

    Returns a dict with:
    - success: bool
    - transcript: list of dicts (if successful)
    - error: str (if failed)
    """
    url = f"https://www.youtube.com/watch?v={video_id}"

    ydl_opts = {
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en'],
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            # Try to get manual subtitles first, then automatic captions
            subtitles = None
            if 'subtitles' in info and 'en' in info['subtitles']:
                subtitles = info['subtitles']['en']
            elif 'automatic_captions' in info and 'en' in info['automatic_captions']:
                subtitles = info['automatic_captions']['en']

            if not subtitles:
                return {
                    'success': False,
                    'error': 'No English transcript or captions available for this video.'
                }

            # Get the subtitle data (ONLY accept json3 format for reliable parsing)
            subtitle_url = None
            for sub in subtitles:
                if sub.get('ext') == 'json3':
                    subtitle_url = sub.get('url')
                    break

            if not subtitle_url:
                return {
                    'success': False,
                    'error': 'JSON subtitle format not available. This video may only have VTT/SRT captions.'
                }

            # Download subtitles with retry logic
            try:
                session = create_retry_session()
                response = session.get(
                    subtitle_url,
                    timeout=(10, 30),
                    headers={'User-Agent': 'Mozilla/5.0 (compatible; YouTubeTranscriptScraper/1.0)'}
                )
                response.raise_for_status()
                subtitle_data = response.json()
            except requests.exceptions.Timeout:
                return {
                    'success': False,
                    'error': 'Connection timed out while fetching subtitles. Please try again.'
                }
            except requests.exceptions.ConnectionError:
                return {
                    'success': False,
                    'error': 'Network connection failed. Please check your internet connection.'
                }
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    return {
                        'success': False,
                        'error': 'Rate limited by YouTube. Please wait a few minutes and try again.'
                    }
                elif e.response.status_code == 403:
                    return {
                        'success': False,
                        'error': 'Access denied. Video may be age-restricted or geo-blocked.'
                    }
                else:
                    return {
                        'success': False,
                        'error': f'HTTP error {e.response.status_code} while fetching subtitles.'
                    }
            except requests.exceptions.RequestException as e:
                return {
                    'success': False,
                    'error': f'Failed to fetch subtitles: {str(e)}'
                }
            except json.JSONDecodeError:
                return {
                    'success': False,
                    'error': 'Invalid subtitle format received.'
                }

            # Convert to transcript format
            transcript = []
            if 'events' in subtitle_data:
                for event in subtitle_data['events']:
                    if 'segs' in event:
                        text = ''.join([seg.get('utf8', '') for seg in event['segs']])
                        if text.strip():
                            transcript.append({
                                'text': text.strip(),
                                'start': event.get('tStartMs', 0) / 1000.0,
                                'duration': event.get('dDurationMs', 0) / 1000.0
                            })

            if not transcript:
                return {
                    'success': False,
                    'error': 'Could not parse transcript data.'
                }

            return {
                'success': True,
                'transcript': transcript,
                'message': 'Transcript retrieved successfully!'
            }

    except Exception as e:
        error_msg = str(e)
        if 'Private video' in error_msg or 'This video is unavailable' in error_msg:
            return {
                'success': False,
                'error': 'Video is unavailable. It may be private, deleted, or restricted.'
            }
        else:
            return {
                'success': False,
                'error': f'Error: {error_msg}'
            }


def format_transcript(transcript_data, include_timestamps=False):
    """
    Format transcript data into readable text.

    Args:
        transcript_data: List of transcript entries
        include_timestamps: Whether to include timestamps in output
    """
    if include_timestamps:
        formatted = []
        for entry in transcript_data:
            timestamp = f"[{int(entry['start'] // 60):02d}:{int(entry['start'] % 60):02d}]"
            formatted.append(f"{timestamp} {entry['text']}")
        return '\n'.join(formatted)
    else:
        return ' '.join([entry['text'] for entry in transcript_data])


def save_transcript_to_file(video_id, transcript_text, output_dir='transcripts'):
    """
    Save transcript to a text file.

    Returns the filepath where the transcript was saved.
    """
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{video_id}_{timestamp}.txt"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(f"YouTube Video ID: {video_id}\n")
        f.write(f"Downloaded: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Video URL: https://www.youtube.com/watch?v={video_id}\n")
        f.write("-" * 80 + "\n\n")
        f.write(transcript_text)

    return filepath


# Streamlit UI
st.set_page_config(
    page_title="YouTube Transcript Scraper",
    page_icon="📝",
    layout="centered"
)

st.title("📝 YouTube Transcript Scraper")
st.write("Extract transcripts from YouTube videos and save them as text files.")

# Input section
url_input = st.text_input(
    "Enter YouTube URL:",
    placeholder="https://www.youtube.com/watch?v=..."
)

# Options
col1, col2 = st.columns(2)
with col1:
    include_timestamps = st.checkbox("Include timestamps", value=False)
with col2:
    auto_save = st.checkbox("Auto-save to file", value=True)

# Extract button
if st.button("Extract Transcript", type="primary"):
    if not url_input:
        st.error("Please enter a YouTube URL.")
    else:
        with st.spinner("Extracting transcript..."):
            # Extract video ID
            video_id = extract_video_id(url_input)

            if not video_id:
                st.error("Invalid YouTube URL format. Please check the URL and try again.")
            else:
                st.info(f"Video ID: `{video_id}`")

                # Fetch transcript
                result = get_transcript(video_id)

                if result['success']:
                    # Format transcript
                    transcript_text = format_transcript(
                        result['transcript'],
                        include_timestamps=include_timestamps
                    )

                    # Success message
                    st.success(result['message'])

                    # Display stats
                    word_count = len(transcript_text.split())
                    st.metric("Word Count", f"{word_count:,}")

                    # Display transcript
                    st.subheader("Transcript:")
                    st.text_area(
                        "Transcript content",
                        transcript_text,
                        height=300,
                        label_visibility="collapsed"
                    )

                    # Auto-save to file
                    if auto_save:
                        try:
                            filepath = save_transcript_to_file(video_id, transcript_text)
                            st.success(f"Saved to: `{filepath}`")
                        except Exception as e:
                            st.warning(f"Could not auto-save: {str(e)}")

                    # Download button
                    st.download_button(
                        label="⬇️ Download as TXT",
                        data=transcript_text,
                        file_name=f"{video_id}_transcript.txt",
                        mime="text/plain"
                    )
                else:
                    # Display error
                    st.error(result['error'])

# Footer
st.divider()
st.caption("Built with Streamlit and yt-dlp")
