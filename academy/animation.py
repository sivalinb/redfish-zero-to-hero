import json
from pathlib import Path
import streamlit as st


def show(concept, accent):
    template = (Path(__file__).parent / "components/animation.html").read_text()
    payload = json.dumps(
        {"title": concept["title"], "animation": concept["animation"], "accent": accent}
    ).replace("</", "<\\/")
    st.iframe(template.replace("__PAYLOAD__", payload), height="content")
