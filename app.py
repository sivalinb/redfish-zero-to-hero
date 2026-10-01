from __future__ import annotations
import html
import json
import os
import pandas as pd
import plotly.express as px
import streamlit as st
import yaml
from academy.course import ROOT, course, glossary
from academy.progress import ProgressStore
from academy.animation import show as animate
from academy.labs import run_lab
from academy.tutor import answer
from academy import redfish, gpu, otel

DATA = course()
st.set_page_config(page_title=DATA["title"], page_icon="🧭", layout="wide")
css = (ROOT / "academy/style.css").read_text()
st.markdown(
    f"<style>{css}:root{{--course-accent:{DATA['accent']};}}</style>", unsafe_allow_html=True
)


@st.cache_resource
def store():
    return ProgressStore(os.getenv("PROGRESS_DB") or None)


if "profile" not in st.session_state:
    profile, token = store().create()
    st.session_state.update(profile=profile, resume_token=token, plans={}, results={}, chat=[])
summary = store().summary(st.session_state.profile)

with st.sidebar:
    st.markdown("<div class='eyebrow'>Infrastructure academy</div>", unsafe_allow_html=True)
    st.header(DATA["title"])
    st.caption("Start with everyday ideas. Build skill through evidence.")
    st.progress(
        len(summary["completed"]) / 11,
        text=f"{len(summary['completed'])} of 11 badges · {summary['xp']} XP",
    )
    level_number = st.selectbox(
        "Learning level",
        list(range(11)),
        format_func=lambda i: f"{i:02d} · {DATA['levels'][i]['title']}",
        key="level_number",
    )
    st.caption("Every lesson can be previewed. Earn badges in order through the assessments.")
    with st.expander("Save or resume progress"):
        st.caption(
            "Download your private resume code. Keep it to continue after a new session. The code grants access to this teaching profile."
        )
        st.download_button(
            "Save my resume code",
            st.session_state.resume_token,
            file_name=f"{DATA['slug']}-resume.txt",
            mime="text/plain",
        )
        resumed = st.text_input("Resume code", type="password", key="resume_input")
        if st.button("Resume profile"):
            identity = store().resume(resumed)
            if identity:
                st.session_state.update(
                    profile=identity, resume_token=resumed.strip(), plans={}, results={}, chat=[]
                )
                st.rerun()
            else:
                st.error("That code does not match a profile in this installation.")
    with st.expander("Course map"):
        for item in DATA["levels"]:
            st.write(f"**{item['number']:02d}** {item['title']}")
    st.caption("No real hardware connection is made by the training UI. No API key is required.")

level = DATA["levels"][level_number]
st.markdown(
    "<div class='eyebrow'>Zero to hero · one clear concept at a time</div>", unsafe_allow_html=True
)
st.title(DATA["title"])
st.markdown(
    f"<div class='intro'>{html.escape(DATA['subtitle'])}</div><span class='mode-pill'>{html.escape(DATA['mode'])}</span>",
    unsafe_allow_html=True,
)
view = st.radio(
    "Workspace",
    ["Learn", "Practice lab", "Assessment", "Tutor", "Progress"],
    horizontal=True,
    key="view",
    label_visibility="collapsed",
)
st.divider()


def render_fields(fields, prefix):
    plan = {}
    for f in fields:
        key = prefix + "-" + f["name"]
        if f["type"] == "choice":
            plan[f["name"]] = st.selectbox(
                f["label"], f["options"], index=f["options"].index(f["default"]), key=key
            )
        elif f["type"] == "bool":
            plan[f["name"]] = st.checkbox(f["label"], value=f["default"], key=key)
        elif f["type"] in ("int", "float"):
            cast = int if f["type"] == "int" else float
            plan[f["name"]] = st.number_input(
                f["label"],
                min_value=cast(f["minimum"]),
                max_value=cast(f["maximum"]),
                value=cast(f["default"]),
                step=cast(f.get("step", 1 if cast is int else 0.1)),
                key=key,
            )
        elif f["type"] == "code":
            plan[f["name"]] = st.text_area(
                f["label"], value=f["default"], height=260, max_chars=6000, key=key
            )
        else:
            plan[f["name"]] = st.text_input(f["label"], value=f["default"], max_chars=300, key=key)
    return plan


def report_view(report):
    data = report.get("data", {})
    if isinstance(data, dict) and "before" in data and "after" in data:
        if DATA["kind"] == "otel":
            st.subheader("Before and after")
            for label in ("before", "after"):
                st.markdown(f"**{label.title()}**")
                otel_view(data[label])
        elif DATA["kind"] == "gpu":
            a, b = st.columns(2)
            for container, key in ((a, "before"), (b, "after")):
                with container:
                    st.markdown(f"**{key.title()}**")
                    replay_view(data[key])
        else:
            a, b = st.columns(2)
            with a:
                st.write("Before")
                st.json(data["before"])
            with b:
                st.write("After")
                st.json(data["after"])
    elif DATA["kind"] == "otel" and isinstance(data, dict) and "spans" in data:
        otel_view(data)
        if "collector" in data:
            st.code(yaml.safe_dump(data["collector"], sort_keys=False), language="yaml")
    elif isinstance(data, dict) and "total_gib" in data:
        cols = st.columns(3)
        cols[0].metric("Estimated use", f"{data['total_gib']:.2f} GiB")
        cols[1].metric("Capacity", f"{data['vram_gib']} GiB")
        cols[2].metric("Fits this estimate", "Yes" if data["fits"] else "No")
        st.caption(data["mode"])
        frame = pd.DataFrame(
            {
                "Contribution": ["Weights", "Optimizer allowance", "Request allowance", "Overhead"],
                "GiB": [
                    data["weights_gib"],
                    data["optimizer_gib"],
                    data["requests_gib"],
                    data["overhead_gib"],
                ],
            }
        )
        st.plotly_chart(
            px.bar(
                frame,
                x="GiB",
                y="Contribution",
                orientation="h",
                color="Contribution",
                color_discrete_sequence=[DATA["accent"], "#86bfae", "#e5b456", "#a5b8c8"],
            ),
            width="stretch",
            key="memory-chart",
        )
    elif isinstance(data, dict) and "telemetry" in data:
        replay_view(data["telemetry"])
    elif isinstance(data, dict) and "allocations" in data:
        st.dataframe(data["allocations"], hide_index=True, width="stretch")
    with st.expander("Inspect all computed evidence", expanded=DATA["kind"] == "redfish"):
        st.json(data)
        if report.get("audit"):
            st.dataframe(report["audit"], hide_index=True, width="stretch")
    st.download_button(
        "Download experiment evidence",
        json.dumps(report, indent=2),
        file_name=f"{DATA['kind']}-level-{level_number}-evidence.json",
        mime="application/json",
    )


def otel_view(data):
    spans = data["spans"]
    a, b, c = st.columns(3)
    a.metric("Recorded spans", len(spans))
    b.metric("Distinct trace IDs", len({s["trace_id"] for s in spans}))
    c.metric("Recorded logs", len(data["logs"]))
    if not spans:
        st.info(
            "No spans were retained in this sample. A probability is not an exact small-batch quota; metrics and logs can still be recorded."
        )
    else:
        frame = pd.DataFrame(spans).sort_values("start_ns")
        origin = frame["start_ns"].min()
        frame["start_ms"] = (frame["start_ns"] - origin) / 1e6
        frame["operation"] = (
            frame["service"] + " · " + frame["name"] + " · " + frame["span_id"].str[-4:]
        )
        fig = px.bar(
            frame,
            x="duration_ms",
            y="operation",
            base="start_ms",
            color="service",
            orientation="h",
            labels={"duration_ms": "Duration (ms)", "start_ms": "Start (ms)"},
            hover_data=["trace_id", "parent_id", "status"],
        )
        fig.update_layout(
            height=min(650, 180 + len(frame) * 22),
            xaxis_title="Milliseconds since first recorded span",
            yaxis_title="",
            legend_title="Producer",
            yaxis={"autorange": "reversed"},
        )
        st.plotly_chart(fig, width="stretch", key=f"trace-{len(spans)}-{spans[0]['span_id']}")
        st.caption(
            "SDK timestamps from local Python execution. These are not a production network benchmark."
        )
    with st.expander("Metrics, logs, and carried headers"):
        st.json({k: data[k] for k in ("metrics", "logs", "carriers")})


def replay_view(rows):
    frame = pd.DataFrame(rows).set_index("second")
    st.caption("Synthetic scenario observations · elapsed seconds on the horizontal axis")
    st.line_chart(
        frame[["temperature_celsius"]], y_label="Temperature (°C)", x_label="Elapsed seconds"
    )
    st.line_chart(frame[["sm_clock_mhz"]], y_label="Clock (MHz)", x_label="Elapsed seconds")
    with st.expander("Memory, utilization, power, and application counts"):
        st.dataframe(frame, width="stretch")


if view == "Learn":
    st.subheader(f"Level {level_number:02d} · {level['title']}")
    st.write(level["objective"])
    if level["prerequisites"]:
        with st.expander("Words to know before this level"):
            for term in level["prerequisites"]:
                st.markdown(
                    f"**{term}:** {glossary().get(term, 'Review this term in the glossary below.')}"
                )
    selected = st.radio(
        "Concept",
        list(range(len(level["concepts"]))),
        format_func=lambda n: level["concepts"][n]["title"],
        horizontal=True,
        key=f"concept-{level_number}",
    )
    concept = level["concepts"][selected]
    animate(concept, DATA["accent"])
    left, right = st.columns([1.6, 1], gap="large")
    with left:
        st.markdown("### What it means")
        st.write(concept["explanation"])
        st.markdown("### Worked example")
        st.markdown(concept["example"])
    with right:
        with st.container(border=True):
            st.markdown("**Picture it**")
            st.write(concept["analogy"])
        with st.container(border=True):
            st.markdown("**A common misunderstanding**")
            st.write(concept["mistake"])
    with st.expander("Words used in this concept"):
        for term in concept["terms"]:
            st.markdown(f"**{term}:** {glossary()[term]}")
    with st.container(border=True):
        st.markdown("**Check your understanding · practice only**")
        q = concept["check"]
        choice = st.radio(q["prompt"], q["options"], index=None, key=f"check-{concept['id']}")
        if st.button("Explain my answer", key=f"explain-{concept['id']}"):
            if choice is None:
                st.info("Choose an answer first.")
            elif q["options"].index(choice) == q["answer"]:
                st.success(q["explanation"])
            else:
                st.info("Review the concept and try again. " + q["explanation"])

    def advance_concept():
        store().mark_read(st.session_state.profile, concept["id"])
        if selected + 1 < len(level["concepts"]):
            st.session_state[f"concept-{level_number}"] = selected + 1
        else:
            st.session_state.view = "Practice lab"

    st.button(
        "I can explain this concept",
        type="primary",
        key=f"read-{concept['id']}",
        on_click=advance_concept,
    )
    with st.expander("Official references"):
        for source in concept["sources"]:
            st.markdown(f"[{source['title']}]({source['url']})")

elif view == "Practice lab":
    st.subheader(f"Level {level_number:02d} · Practice with evidence")
    st.write(level["lab"]["goal"])
    st.caption(
        "This preview helps you learn. The assessment re-runs your plan on two scenarios before awarding a badge."
    )
    with st.form(f"lab-{level_number}"):
        plan = render_fields(level["lab"]["fields"], f"lab-{level_number}")
        variant = st.selectbox(
            "Scenario to inspect",
            [0, 1],
            format_func=lambda v: "Scenario 1" if v == 0 else "Scenario 2 · changed conditions",
        )
        submitted = st.form_submit_button("Run my experiment", type="primary")
    if submitted:
        st.session_state.plans[level_number] = plan
        try:
            st.session_state.results[level_number] = run_lab(
                DATA["kind"], level_number, plan, variant
            )
        except (ValueError, KeyError, IndexError, TypeError) as error:
            st.session_state.results[level_number] = {
                "passed": False,
                "message": str(error),
                "data": {},
            }
    result = st.session_state.results.get(level_number)
    if result:
        (st.success if result["passed"] else st.info)(result["message"])
        report_view(result)
    with st.expander("Hints, one step at a time"):
        hint = st.select_slider(
            "Hint depth", options=[1, 2, 3], value=1, key=f"hint-{level_number}"
        )
        for text in level["lab"]["hints"][:hint]:
            st.write(text)
    with st.expander("Open exploration sandbox"):
        if DATA["kind"] == "redfish":
            scenario = st.selectbox(
                "Simulator condition",
                ["healthy", "fan_failure", "host_down", "transient"],
                key="rf-scenario",
            )
            role = st.selectbox("Console role", ["viewer", "operator"], key="rf-role")
            identity = (scenario, role)
            if st.session_state.get("sim_identity") != identity:
                sim = redfish.Simulator(scenario)
                st.session_state.update(sim=sim, sim_token=sim.login(role), sim_identity=identity)
            sim = st.session_state.sim
            interface = st.radio(
                "Console interface", ["Redfish", "IPMI teaching comparison"], horizontal=True
            )
            if interface == "Redfish":
                method = st.selectbox("Method", ["GET", "POST", "PATCH"])
                path = st.text_input("Local resource path", "/redfish/v1/", max_chars=250)
                body = st.text_area("JSON request body", "{}", max_chars=6000)
                confirm = st.checkbox("I reviewed this simulated change")
                if st.button("Send to simulator"):
                    try:
                        parsed = json.loads(body)
                        if not isinstance(parsed, dict):
                            raise ValueError("Request body must be a JSON object.")
                        response = sim.request(
                            method, path, parsed, st.session_state.sim_token, confirm
                        )
                        st.write(f"HTTP {response.status}")
                        st.json(response.body)
                    except (ValueError, TypeError) as error:
                        st.error(str(error))
            else:
                command = st.text_input("Teaching command", "ipmitool sensor", max_chars=200)
                confirm = st.checkbox("I reviewed this simulated IPMI change")
                if st.button("Compare command result"):
                    try:
                        st.json(sim.ipmi(command, st.session_state.sim_token, confirm))
                    except ValueError as error:
                        st.error(str(error))
            st.caption(
                "Supported paths and actions are discoverable through GET. IPMI commands are mappings, not native transport. /training/ maintenance paths are our own teaching extension."
            )
        elif DATA["kind"] == "gpu":
            st.write("Change the assumptions and inspect the resulting estimate.")
            parameters = st.number_input("Parameters (billions)", 0.1, 1000.0, 7.0)
            dtype = st.selectbox("Sandbox precision", ["FP32", "FP16", "INT8"])
            capacity = st.number_input("Sandbox VRAM (GiB)", 1, 1024, 24)
            batch = st.number_input("Sandbox batch", 1, 256, 1)
            training = st.checkbox("Include the declared training optimizer allowance")
            st.json(gpu.memory_plan(parameters, dtype, capacity, batch, training=training))
            replay_case = st.selectbox(
                "Telemetry replay",
                ["healthy", "memory_pressure", "thermal", "input_starved", "gpu_error"],
            )
            replay_view(gpu.replay(replay_case))
            st.code(gpu.exposition(gpu.replay(replay_case)), language="text")
            upload = st.file_uploader(
                "Optional: inspect your own numeric /metrics text locally",
                type=["txt", "prom"],
                max_upload_size=1,
            )
            if upload:
                try:
                    st.dataframe(gpu.parse_metrics(upload.getvalue().decode("utf-8")))
                except (ValueError, UnicodeError) as error:
                    st.error(str(error))
        else:
            st.write(
                "Inspect a Collector config structurally. Run the optional container example to test an actual Collector."
            )
            config = st.text_area(
                "Collector YAML",
                yaml.safe_dump(otel.collector_config(), sort_keys=False),
                height=300,
            )
            if st.button("Check configuration references"):
                try:
                    st.json(otel.validate_collector(yaml.safe_load(config)))
                except (ValueError, yaml.YAMLError) as error:
                    st.error(str(error))

elif view == "Assessment":
    st.subheader(f"Level {level_number:02d} · Earn {level['badge']}")
    st.write(
        "Pass at least four of five questions and a practical plan that works on both scenarios. Attempts are graded by the server; retries retain useful feedback."
    )
    if level_number > summary["unlocked"]:
        st.info(
            f"You can study this level now. Earn Level {summary['unlocked']}'s badge before taking this assessment."
        )
    elif level_number not in st.session_state.plans:
        st.info("Run a practice lab first. Its plan will be re-tested here.")
    else:
        attempt = store().start(st.session_state.profile, level_number)
        questions = {q["id"]: q for q in level["questions"]}
        with st.form(f"assessment-{attempt['id']}"):
            answers = {}
            for ident in json.loads(attempt["questions"]):
                q = questions[ident]
                selected = st.radio(
                    q["prompt"], q["options"], index=None, key=f"answer-{attempt['id']}-{ident}"
                )
                if selected is not None:
                    answers[ident] = q["options"].index(selected)
            submitted = st.form_submit_button("Grade quiz and re-test my plan", type="primary")
        if submitted:
            result = store().grade(
                st.session_state.profile,
                attempt["id"],
                answers,
                st.session_state.plans[level_number],
            )
            st.session_state[f"grade-{level_number}"] = result
        result = st.session_state.get(f"grade-{level_number}")
        if result:
            if result["passed"]:
                st.success(
                    f"Badge earned: {level['badge']} · {result['score']}% quiz · both practical scenarios passed."
                )

                def continue_level():
                    st.session_state.level_number = level_number + 1
                    st.session_state.view = "Learn"

                if level_number < 10:
                    st.button("Continue to the next level", type="primary", on_click=continue_level)
            else:
                st.info(
                    f"Quiz: {result['score']}%. Review the feedback and refine your plan before another attempt."
                )
            for item in result["feedback"]:
                with st.expander(("✓ " if item["correct"] else "Review · ") + item["question"]):
                    st.write(item["explanation"])
            st.json(result["lab"])

elif view == "Tutor":
    st.subheader("Ask until it makes sense")
    topics = {concept["id"]: concept for item in DATA["levels"] for concept in item["concepts"]}
    current_topic = level["concepts"][st.session_state.get(f"concept-{level_number}", 0)]["id"]
    topic_id = st.selectbox(
        "Concept I am asking about",
        list(topics),
        index=list(topics).index(current_topic),
        format_func=lambda ident: topics[ident]["title"],
        key=f"tutor-topic-{level_number}",
    )
    model_mode = bool(os.getenv("TUTOR_OLLAMA_URL") and os.getenv("TUTOR_MODEL"))
    st.caption(
        "Conversational generation uses the configured model and retrieved course material."
        if model_mode
        else "Offline teaching guide: detailed authored explanations retrieved from this course. Configure Ollama for conversational generation."
    )
    st.write(
        "Try: “Explain this as if I have never managed a server” or ask about a named concept, units, a comparison, or your current lab evidence."
    )
    for message in st.session_state.chat[-12:]:
        with st.chat_message(message["role"]):
            st.markdown(message["body"])
    question = st.chat_input("Ask a course question", max_chars=1500)
    if question:
        evidence = st.session_state.results.get(level_number, {})
        try:
            result = answer(question, evidence, topic_id)
            body = (
                result["answer"]
                + "\n\n"
                + " · ".join(f"[{s['title']}]({s['url']})" for s in result["sources"])
            )
            st.session_state.chat += [
                {"role": "user", "body": question},
                {"role": "assistant", "body": body},
            ]
            st.rerun()
        except ValueError as error:
            st.error(str(error))
    st.caption("The tutor cannot award badges, execute shell commands, or operate equipment.")

else:
    st.subheader("Your learning milestones")
    latest = store().summary(st.session_state.profile)
    a, b, c = st.columns(3)
    a.metric("Badges", f"{len(latest['completed'])} / 11")
    b.metric("Experience", latest["xp"])
    c.metric("Concepts reflected on", f"{len(latest['readings'])} / 33")
    completed = {row["level"] for row in latest["completed"]}
    for offset in range(0, 11, 3):
        cols = st.columns(3)
        for position, item in enumerate(DATA["levels"][offset : offset + 3]):
            earned = item["number"] in completed
            with cols[position]:
                st.markdown(
                    f"<div class='badge {'earned' if earned else 'locked'}'><small>Level {item['number']:02d} · {'Earned' if earned else 'In progress'}</small><strong>{html.escape(item['badge'])}</strong>{html.escape(item['title'])}</div>",
                    unsafe_allow_html=True,
                )
    st.download_button(
        "Download my learning record",
        json.dumps({"course": DATA["title"], **latest}, indent=2),
        file_name=DATA["slug"] + "-learning-record.json",
        mime="application/json",
    )
    st.caption(
        "Badges mark these course objectives. They are not official certification or evidence of untested real-hardware skills."
    )

with st.expander("Find a word in the beginner glossary"):
    search = st.text_input("Word or phrase", max_chars=80, key="glossary-search")
    for term, definition in glossary().items():
        if search and search.lower() in (term + " " + definition).lower():
            st.markdown(f"**{term}:** {definition}")
    if not search:
        st.caption(
            "Type a term such as memory, span, or capacity. Every concept also lists its own words."
        )
