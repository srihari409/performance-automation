import os
import sys
import json
import pathlib
import requests

"""
Usage:
  python upload_panels_to_slack.py <folder_or_file> <token> <channel_id> "<message>" [--zip]

Examples:
  # Upload all PNGs in folder (threaded under one message)
  python upload_panels_to_slack.py C:\Tools\panels_30m xoxb-*** C0123456789 "Grafana panels – Last 30 minutes"

  # Upload a ZIP file only (threaded under one message)
  python upload_panels_to_slack.py C:\Tools\panels_30m.zip xoxb-*** C0123456789 "Grafana panels ZIP – Last 30 minutes" --zip
"""

def slack_api(method: str, token: str, payload: dict):
    url = f"https://slack.com/api/{method}"
    r = requests.post(url, headers={"Authorization": f"Bearer {token}"}, data=payload, timeout=60)
    try:
        return r.status_code, r.text, r.json()
    except Exception:
        return r.status_code, r.text, {}

def upload_file_external(token: str, channel_id: str, file_path: str, title: str, thread_ts: str | None = None):
    file_size = os.path.getsize(file_path)
    filename = os.path.basename(file_path)

    # 1) Get an upload URL
    st, txt, j = slack_api("files.getUploadURLExternal", token, {
        "filename": filename,
        "length": str(file_size)
    })
    if not j.get("ok"):
        return False, f"files.getUploadURLExternal failed: {txt}"

    upload_url = j["upload_url"]
    file_id = j["file_id"]

    # 2) Upload bytes to the returned URL
    with open(file_path, "rb") as f:
        put = requests.post(upload_url, files={"file": (filename, f)}, timeout=300)
    if put.status_code not in (200, 201, 204):
        return False, f"Upload to external URL failed: HTTP {put.status_code} {put.text}"

    # 3) Complete the upload (share in channel, optionally in thread)
    payload = {
        "files": json.dumps([{"id": file_id, "title": title}]),
        "channel_id": channel_id
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts

    st, txt, j2 = slack_api("files.completeUploadExternal", token, payload)
    if not j2.get("ok"):
        return False, f"files.completeUploadExternal failed: {txt}"

    return True, "ok"

def main():
    if len(sys.argv) < 5:
        print("ERROR: Missing args.\n"
              "python upload_panels_to_slack.py <folder_or_file> <token> <channel_id> \"<message>\" [--zip]")
        sys.exit(1)

    path_arg = sys.argv[1]
    token = sys.argv[2]
    channel_id = sys.argv[3]
    message = sys.argv[4]
    zip_mode = ("--zip" in sys.argv)

    p = pathlib.Path(path_arg)
    if not p.exists():
        print(f"ERROR: Path not found: {path_arg}")
        sys.exit(1)

    # 1) Send parent message first (get thread_ts)
    st, txt, j = slack_api("chat.postMessage", token, {"channel": channel_id, "text": message})
    if not j.get("ok"):
        print(f"ERROR sending Slack message: {txt}")
        sys.exit(2)

    thread_ts = j.get("ts")
    print(f"Slack message sent. thread_ts={thread_ts}")

    # 2) Upload either ZIP only OR all PNGs
    if zip_mode:
        if not p.is_file():
            print("ERROR: --zip expects a file path (e.g., panels.zip)")
            sys.exit(3)
        title = p.stem
        print(f"Uploading ZIP {p.name} in thread...")
        ok, info = upload_file_external(token, channel_id, str(p), title, thread_ts=thread_ts)
        if ok:
            print(f"✅ Uploaded {p.name}")
        else:
            print(f"❌ Failed {p.name}: {info}")
            sys.exit(4)
        return

    # Folder mode: upload all PNGs
    if p.is_file():
        print("ERROR: expected a folder path containing PNGs (or use --zip for a file)")
        sys.exit(3)

    files = sorted([x for x in p.glob("*.png")])
    if not files:
        print("No PNG files found to upload.")
        return

    ok_count = 0
    for file_path in files:
        title = file_path.stem
        print(f"Uploading {file_path.name} in thread...")
        ok, info = upload_file_external(token, channel_id, str(file_path), title, thread_ts=thread_ts)
        if ok:
            ok_count += 1
            print(f"✅ Uploaded {file_path.name}")
        else:
            print(f"❌ Failed {file_path.name}: {info}")

    print(f"Done. Uploaded {ok_count}/{len(files)} files in thread.")

if __name__ == "__main__":
    main()
