"""
SIMPLE Recorder Dashboard - Appium Inspector Style
Clean UI with live device screen and direct element tapping
"""

import base64
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

# Setup path
SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from appium_utils import MobileInspector, TestRunner  # noqa: E402
from appium_utils.action_logger import ActionLogger  # noqa: E402

# Page config
st.set_page_config(page_title="Simple Recorder", page_icon="🎯", layout="wide")

# Initialize session state
if "runner" not in st.session_state:
    st.session_state.runner = TestRunner()
if "inspector" not in st.session_state:
    st.session_state.inspector = MobileInspector()
if "action_logger" not in st.session_state:
    st.session_state.action_logger = ActionLogger()
if "connected" not in st.session_state:
    st.session_state.connected = False
if "recording" not in st.session_state:
    st.session_state.recording = False
if "device_profiles" not in st.session_state:
    try:
        st.session_state.device_profiles = st.session_state.runner.get_device_profiles()
    except Exception:
        st.session_state.device_profiles = []
if "current_screen" not in st.session_state:
    st.session_state.current_screen = None

# Header
st.title("🎯 Simple Recorder")

# Connection bar
col1, col2, col3 = st.columns([2, 1, 1])

with col1:
    if st.session_state.device_profiles:
        profile_options = {
            f"{p['name']} ({p['platform']})": p["id"] for p in st.session_state.device_profiles
        }
        selected_profile_name = st.selectbox("Device", options=list(profile_options.keys()))
        selected_profile_id = profile_options[selected_profile_name]
    else:
        st.warning("No device profiles found")
        selected_profile_id = None

with col2:
    if not st.session_state.connected:
        if st.button(
            "🔌 Connect", type="primary", use_container_width=True, disabled=not selected_profile_id
        ):
            success, msg = st.session_state.runner.connect_from_config(selected_profile_id)
            if success:
                st.session_state.connected = True
                st.session_state.inspector.driver = st.session_state.runner.driver
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)
    else:
        st.success("✅ Connected")

with col3:
    if st.session_state.connected:
        recording_toggle = st.toggle("🔴 Record", value=st.session_state.recording)
        if recording_toggle != st.session_state.recording:
            st.session_state.recording = recording_toggle
            st.rerun()

st.divider()

# Main content
if st.session_state.connected:
    col_screen, col_actions = st.columns([2, 1])

    with col_screen:
        st.subheader("📱 Device Screen")

        # Refresh button
        if st.button("🔄 Refresh Screen", use_container_width=True):
            data = st.session_state.inspector.capture_screen()
            if "error" not in data:
                st.session_state.current_screen = data
                st.rerun()

        # Show device screen
        if st.session_state.current_screen:
            screen = st.session_state.current_screen

            # Display screenshot
            if screen.get("screenshot"):
                screenshot_data = base64.b64decode(screen["screenshot"])
                st.image(screenshot_data, use_container_width=True)

            # Show clickable elements
            st.subheader("🖱️ Tap on Elements")
            elements = screen.get("elements", [])

            # Filter clickable or has text
            interactive = [
                e for e in elements if e.get("clickable") or e.get("text") or e.get("resource-id")
            ]

            if interactive:
                st.caption(f"{len(interactive)} interactive elements found")

                # Show elements in a clean list
                for i, elem in enumerate(interactive[:15]):  # Show top 15
                    elem_text = elem.get("text") or elem.get("content-desc") or "No text"
                    elem_type = elem.get("type", "Unknown")
                    elem_id = elem.get("resource-id", "")

                    # Create clickable element
                    with st.container():
                        col_info, col_btn = st.columns([4, 1])

                        with col_info:
                            if elem_text != "No text":
                                st.markdown(f"**{elem_type}**: {elem_text[:50]}")
                            else:
                                st.markdown(f"**{elem_type}**")
                            if elem_id:
                                st.caption(f"ID: {elem_id.split('/')[-1]}")

                        with col_btn:
                            if st.button("👆", key=f"tap_{i}"):
                                if st.session_state.recording:
                                    action = st.session_state.action_logger.log_tap(elem)
                                    st.success("✅ Logged!")
                                    st.rerun()
                                else:
                                    st.warning("Enable recording first!")

                        st.divider()
            else:
                st.info("No interactive elements found. Click Refresh.")

        else:
            st.info("👆 Click 'Refresh Screen' to see device")

    with col_actions:
        st.subheader("📋 Logged Actions")

        actions = st.session_state.action_logger.get_actions()

        if actions:
            st.write(f"**{len(actions)} actions**")

            # Show logged actions
            for i, action in enumerate(actions[-10:], 1):  # Last 10
                with st.expander(
                    f"{i}. {action['description'][:40]}...", expanded=(i == len(actions))
                ):
                    st.caption(action["description"])

            st.divider()

            # Quick actions
            st.subheader("⚡ Quick")

            col_swipe1, col_swipe2 = st.columns(2)
            with col_swipe1:
                if st.button("⬆️", use_container_width=True):
                    st.session_state.action_logger.log_swipe("up")
                    st.rerun()
                if st.button("⬇️", use_container_width=True):
                    st.session_state.action_logger.log_swipe("down")
                    st.rerun()

            with col_swipe2:
                if st.button("⬅️", use_container_width=True):
                    st.session_state.action_logger.log_swipe("left")
                    st.rerun()
                if st.button("➡️", use_container_width=True):
                    st.session_state.action_logger.log_swipe("right")
                    st.rerun()

            if st.button("🔙 Back", use_container_width=True):
                st.session_state.action_logger.log_back()
                st.rerun()

            st.divider()

            # Generate test
            if st.button("🐍 Generate Test", type="primary", use_container_width=True):
                platform = "android"  # Default
                code = st.session_state.action_logger.generate_code(platform)

                with st.expander("📝 Generated Code", expanded=True):
                    st.code(code, language="python")

                    filename = f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"
                    st.download_button(
                        "💾 Download",
                        data=code,
                        file_name=filename,
                        mime="text/x-python",
                        use_container_width=True,
                    )

            # Clear actions
            if st.button("🗑️ Clear All", use_container_width=True):
                st.session_state.action_logger.clear()
                st.rerun()

        else:
            if st.session_state.recording:
                st.info("📝 Tap elements on the left to log actions")
            else:
                st.warning("🔴 Enable recording first!")

else:
    st.info("👈 Connect to a device to start")
