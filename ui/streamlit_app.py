"""Minimal chat UI for demos.

    pip install -e ".[ui]"
    API_URL=http://localhost:8000 streamlit run ui/streamlit_app.py

For the deployed API also set API_TOKEN (see scripts/get_token.sh).
"""

import os
import uuid

import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
TOKEN = os.environ.get("API_TOKEN")
HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

st.set_page_config(page_title="FieldDocs", page_icon="🛠️")
st.title("FieldDocs — field engineer assistant")
st.caption("Answers come only from indexed manuals, with citations.")

if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex[:16]
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if question := st.chat_input("Ask about a procedure, alarm or fault code"):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"), st.spinner("Searching the manuals..."):
        resp = requests.post(
            f"{API_URL}/v1/chat",
            json={"question": question, "session_id": st.session_state.session_id},
            headers=HEADERS,
            timeout=60,
        )
        if resp.status_code != 200:
            st.error(f"API error {resp.status_code}: {resp.text[:300]}")
        else:
            data = resp.json()
            st.markdown(data["answer"])
            cited = [s for s in data["sources"] if s["cited"]]
            if cited:
                with st.expander("Sources"):
                    for s in cited:
                        page = f", page {s['page']}" if s.get("page") else ""
                        st.markdown(f"**[{s['index']}] {s['source']}{page}** — {s['snippet']}")
            st.caption(
                f"{data['latency_ms']} ms · {data['usage']['input_tokens']}+"
                f"{data['usage']['output_tokens']} tokens · cached={data['cached']}"
            )
            st.session_state.messages.append({"role": "assistant", "content": data["answer"]})
            st.session_state.last_trace = data["trace_id"]

# Feedback for the latest answer (outside the input block so it survives reruns).
if trace := st.session_state.get("last_trace"):
    col1, col2 = st.columns(2)
    for col, label, score in ((col1, "👍 Helpful", 1), (col2, "👎 Not helpful", 0)):
        if col.button(label, key=f"{score}-{trace}"):
            requests.post(
                f"{API_URL}/v1/feedback",
                json={"trace_id": trace, "score": score},
                headers=HEADERS,
                timeout=10,
            )
            st.session_state.last_trace = None
            st.toast("Thanks for the feedback")
