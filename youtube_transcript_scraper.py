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


def extract_items_from_transcript(transcript_text, list_name, video_url):
    """Use Claude Haiku to extract structured items from a transcript."""
    import anthropic

    # Auto-detect extraction type from list name
    list_name_lower = list_name.lower()
    if any(word in list_name_lower for word in ["restaurant", "food", "meal", "eat", "dining"]):
        extraction_hint = "restaurants and food recommendations"
        item_hint = "restaurant name, cuisine type, location, specific dishes recommended"
    elif any(word in list_name_lower for word in ["recipe", "cook", "bake"]):
        extraction_hint = "recipes"
        item_hint = "recipe name, key ingredients, brief instructions"
    elif any(word in list_name_lower for word in ["book", "read"]):
        extraction_hint = "books"
        item_hint = "book title, author, why it was recommended"
    elif any(word in list_name_lower for word in ["movie", "watch", "film", "show"]):
        extraction_hint = "movies and shows"
        item_hint = "title, genre, why it was recommended"
    elif any(word in list_name_lower for word in ["wine", "drink", "beer", "cocktail"]):
        extraction_hint = "drinks and beverages"
        item_hint = "drink name, type, tasting notes or pairing suggestions"
    else:
        extraction_hint = "notable items, recommendations, or actionable information"
        item_hint = "item name, relevant details"

    # Truncate transcript to stay within reasonable token limits
    max_chars = 100000  # ~25K words, covers most videos up to ~2.5 hours
    truncated = transcript_text[:max_chars]
    was_truncated = len(transcript_text) > max_chars

    truncation_note = ""
    if was_truncated:
        truncation_note = "\nNote: This transcript was truncated. Focus on the content provided."

    prompt = f"""Extract {extraction_hint} from this YouTube video transcript.

Return a JSON array of items. Each item should have:
- "name": The item name (required)
- "notes": Additional details ({item_hint})

Only extract items that are clearly mentioned in the transcript. Do not infer or fabricate items.
If no relevant items are found, return an empty array: []
{truncation_note}
Transcript:
{truncated}

Respond ONLY with the JSON array, no other text."""

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model="claude-3-5-haiku-20241022",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )

    response_text = response.content[0].text.strip()

    # Handle markdown code blocks (same pattern as url_import_service.py)
    if response_text.startswith("```"):
        response_text = response_text.split("```")[1]
        if response_text.startswith("json"):
            response_text = response_text[4:]
        response_text = response_text.strip()

    items = json.loads(response_text)

    # Normalize to {name, notes} and append source URL
    result = []
    for item in items:
        if isinstance(item, dict) and "name" in item:
            notes = item.get("notes", "")
            if notes:
                notes += f"\n\nSource: {video_url}"
            else:
                notes = f"Source: {video_url}"
            result.append({"name": item["name"], "notes": notes})
        elif isinstance(item, str):
            result.append({"name": item, "notes": f"Source: {video_url}"})

    return result


# --- Streamlit UI ---

st.set_page_config(
    page_title="YouTube Transcript Scraper",
    page_icon="📝",
    layout="centered"
)

# Initialize session state
if "transcript_text" not in st.session_state:
    st.session_state.transcript_text = None
    st.session_state.video_id = None
    st.session_state.extracted_items = None
    st.session_state.personal_lists = None

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
        # Reset extraction state when fetching a new transcript
        st.session_state.extracted_items = None
        st.session_state.personal_lists = None

        with st.spinner("Extracting transcript..."):
            video_id = extract_video_id(url_input)

            if not video_id:
                st.error("Invalid YouTube URL format. Please check the URL and try again.")
            else:
                st.info(f"Video ID: `{video_id}`")
                result = get_transcript(video_id)

                if result['success']:
                    transcript_text = format_transcript(
                        result['transcript'],
                        include_timestamps=include_timestamps
                    )
                    # Store in session state
                    st.session_state.transcript_text = transcript_text
                    st.session_state.video_id = video_id
                    st.success(result['message'])
                else:
                    st.session_state.transcript_text = None
                    st.session_state.video_id = None
                    st.error(result['error'])

# Display transcript from session state
if st.session_state.transcript_text:
    transcript_text = st.session_state.transcript_text
    video_id = st.session_state.video_id

    word_count = len(transcript_text.split())
    st.metric("Word Count", f"{word_count:,}")

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

    # --- AI Extraction Section ---
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    api_url = os.environ.get("DAILY_BRIEFING_API_URL", "").rstrip("/")

    if api_key and api_url:
        st.divider()
        st.subheader("Extract with AI")

        # Fetch available lists from Daily Briefing API
        if st.session_state.personal_lists is None:
            try:
                resp = requests.get(f"{api_url}/api/personal-lists", timeout=5)
                if resp.ok:
                    st.session_state.personal_lists = resp.json()
                else:
                    st.session_state.personal_lists = []
            except Exception:
                st.session_state.personal_lists = []

        lists = st.session_state.personal_lists
        if not lists:
            st.warning("No personal lists found. Create lists in the Daily Briefing app first.")
        else:
            # List selector
            list_options = {
                f"{lst.get('emoji', '')} {lst['name']}".strip(): lst
                for lst in lists
            }
            selected_label = st.selectbox("Save to list:", list(list_options.keys()))
            selected_list = list_options[selected_label]

            # Extract button
            if st.button("Extract Items", type="secondary"):
                with st.spinner("Extracting items with AI..."):
                    try:
                        video_url = f"https://www.youtube.com/watch?v={video_id}"
                        items = extract_items_from_transcript(
                            transcript_text,
                            selected_list["name"],
                            video_url
                        )
                        st.session_state.extracted_items = items
                    except json.JSONDecodeError:
                        st.error("AI returned an unexpected format. Please try again.")
                        st.session_state.extracted_items = None
                    except Exception as e:
                        st.error(f"Extraction failed: {str(e)}")
                        st.session_state.extracted_items = None

            # Preview and save
            if st.session_state.extracted_items is not None:
                items = st.session_state.extracted_items
                if not items:
                    st.info("No relevant items found in the transcript for this list type.")
                else:
                    st.write(f"Found **{len(items)}** items:")

                    # Checkboxes for each item
                    selected = []
                    for i, item in enumerate(items):
                        # Show first line of notes (before source URL)
                        preview = ""
                        if item.get("notes"):
                            first_line = item["notes"].split("\n")[0]
                            if first_line and not first_line.startswith("Source:"):
                                preview = f" — {first_line}"
                        checked = st.checkbox(
                            f"**{item['name']}**{preview}",
                            value=True,
                            key=f"item_{i}"
                        )
                        if checked:
                            selected.append(item)

                    # Save button
                    if selected and st.button(
                        f"Save {len(selected)} items to {selected_list['name']}",
                        type="primary"
                    ):
                        with st.spinner("Saving..."):
                            batch = [
                                {"content": item["name"], "notes": item.get("notes")}
                                for item in selected
                            ]
                            try:
                                resp = requests.post(
                                    f"{api_url}/api/personal-lists/{selected_list['id']}/items/batch",
                                    json={"items": batch},
                                    timeout=10
                                )
                                if resp.ok:
                                    st.success(f"Saved {len(selected)} items to {selected_list['name']}!")
                                    st.session_state.extracted_items = None
                                else:
                                    st.error(f"Save failed (HTTP {resp.status_code})")
                            except Exception as e:
                                st.error(f"Save failed: {str(e)}")

# Footer
st.divider()
st.caption("Built with Streamlit and yt-dlp")
