"""
"Help & Assistant" tab: a simple canned-response chat helper.
"""

from __future__ import annotations

import streamlit as st


def render() -> None:
    st.subheader("Tracker Assistant Bot")
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = [{"role": "assistant", "content": "How can I assist you today?"}]
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])
    if user_query := st.chat_input("Ask a question..."):
        st.session_state.chat_history.append({"role": "user", "content": user_query})
        with st.chat_message("user"):
            st.write(user_query)
        response = "Use the navigation menu above to manage employees, leaves, and credits!"
        st.session_state.chat_history.append({"role": "assistant", "content": response})
        with st.chat_message("assistant"):
            st.write(response)
