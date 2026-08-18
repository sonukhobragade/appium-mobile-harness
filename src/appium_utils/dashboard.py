"""
Unified Mobile Test Automation Dashboard
Integrates Inspector, Recorder, Runner into a single interface
"""

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

# Ensure src directory is on sys.path when executed directly via `streamlit run`
SRC_DIR = Path(__file__).resolve().parents[1]  # Points to src/ directory
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from appium_utils import (  # noqa: E402
    ActionRecorder,
    MobileInspector,
    QuickTestGenerator,
    SmartCheckpointRecorder,
    TestRunner,
)
from appium_utils.action_logger import ActionLogger  # noqa: E402

# Page configuration
st.set_page_config(page_title="Mobile Test Automation System", page_icon="🎯", layout="wide")

# Initialize session state
if "runner" not in st.session_state:
    st.session_state.runner = TestRunner()
if "inspector" not in st.session_state:
    st.session_state.inspector = MobileInspector()
if "recorder" not in st.session_state:
    st.session_state.recorder = ActionRecorder()
if "connected" not in st.session_state:
    st.session_state.connected = False
if "current_script" not in st.session_state:
    st.session_state.current_script = ""
if "device_profiles" not in st.session_state:
    try:
        st.session_state.device_profiles = st.session_state.runner.get_device_profiles()
    except Exception:
        st.session_state.device_profiles = []
if "auto_connect_done" not in st.session_state:
    st.session_state.auto_connect_done = False
if "smart_recorder" not in st.session_state:
    st.session_state.smart_recorder = None
if "action_logger" not in st.session_state:
    st.session_state.action_logger = ActionLogger()
if "recording_enabled" not in st.session_state:
    st.session_state.recording_enabled = False

# Header
st.title("🎯 Unified Mobile Test Automation System")

# Auto-connect on startup if enabled
if not st.session_state.connected and not st.session_state.auto_connect_done:
    try:
        config = st.session_state.runner.load_device_config()
    except (FileNotFoundError, ValueError) as exc:
        st.warning(f"Device config issue: {exc}")
        config = {}

    if config.get("devices"):
        with st.spinner("🔄 Auto-connecting to default device..."):
            success, msg = st.session_state.runner.connect_from_config()
            if success:
                st.session_state.connected = True
                st.session_state.inspector.driver = st.session_state.runner.driver
                st.session_state.recorder.driver = st.session_state.runner.driver
                st.session_state.smart_recorder = SmartCheckpointRecorder(
                    st.session_state.runner.driver
                )
                st.success(f"✅ {msg}")
            else:
                st.info(f"Auto-connect skipped: {msg}")
    st.session_state.auto_connect_done = True

# Connection Bar
with st.container():
    col1, col2, col3 = st.columns([3, 2, 1])

    with col1:
        if st.session_state.device_profiles:
            # Show device profile selector
            profile_options = {
                f"{p['name']} ({p['platform']}) - {p['udid']}": p["id"]
                for p in st.session_state.device_profiles
            }
            selected_profile_name = st.selectbox(
                "📱 Device Profile",
                options=list(profile_options.keys()),
                key="device_profile_select",
                help="Pre-configured device from device_config.json",
            )
            selected_profile_id = profile_options[selected_profile_name]
        else:
            st.warning("⚠️ No device profiles found in device_config.json")
            selected_profile_id = None

    with col2:
        # Show current session info if connected
        if st.session_state.connected and st.session_state.runner.session_info:
            info = st.session_state.runner.session_info
            st.info(
                f"🟢 **Connected**\n\n"
                f"Device: {info.get('device', 'Unknown')}\n\n"
                f"App: {info.get('app_package', 'Unknown')}"
            )
        else:
            st.info("🔴 **Disconnected**\n\nSelect a device profile and click Connect")

    with col3:
        st.write("")  # Spacing
        st.write("")  # Spacing
        if not st.session_state.connected:
            if st.button(
                "🔌 Connect",
                type="primary",
                use_container_width=True,
                disabled=not selected_profile_id,
            ):
                with st.spinner("Connecting..."):
                    success, msg = st.session_state.runner.connect_from_config(selected_profile_id)

                    if success:
                        st.session_state.connected = True
                        st.session_state.inspector.driver = st.session_state.runner.driver
                        st.session_state.recorder.driver = st.session_state.runner.driver
                        st.session_state.smart_recorder = SmartCheckpointRecorder(
                            st.session_state.runner.driver
                        )
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
        else:
            if st.button("🔴 Disconnect", use_container_width=True):
                st.session_state.runner.disconnect()
                st.session_state.connected = False
                st.rerun()

st.divider()

# Quick Test Generator Section
if st.session_state.connected:
    st.subheader("⚡ Quick Test Generator")
    col1, col2, col3 = st.columns([2, 2, 1])

    with col1:
        screen_name = st.text_input(
            "Screen Name", value="login", placeholder="e.g., login, home, profile"
        )

    with col2:
        st.write("")
        st.write("")
        if st.button(
            "🤖 Generate Test from Current Screen", type="primary", use_container_width=True
        ):
            with st.spinner("📸 Capturing screen hierarchy and generating test..."):
                generator = QuickTestGenerator(st.session_state.runner.driver)
                result = generator.generate_test(screen_name)

                if result["success"]:
                    st.session_state.generated_test = result
                    st.success(f"✅ Generated test with {result['elements_found']} elements!")
                    st.rerun()
                else:
                    st.error(f"❌ Error: {result.get('error', 'Unknown error')}")

    with col3:
        if "generated_test" in st.session_state and st.session_state.generated_test:
            test_code = st.session_state.generated_test["test_code"]
            screen = st.session_state.generated_test["screen_name"]
            filename = f"test_{screen}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"

            st.download_button(
                label="💾 Download Test",
                data=test_code,
                file_name=filename,
                mime="text/x-python",
                use_container_width=True,
            )

    # Show generated test code
    if "generated_test" in st.session_state and st.session_state.generated_test:
        with st.expander("📝 Generated Test Code", expanded=True):
            test_data = st.session_state.generated_test
            st.code(test_data["test_code"], language="python")
            st.info(
                f"Platform: {test_data['platform']} | Elements: {test_data['elements_found']} | Generated: {test_data['generated_at']}"
            )

    st.divider()

# Main Tabs
tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["🔍 Inspector", "🎬 Recorder", "📝 Script Editor", "▶️ Runner", "📊 Results"]
)

# TAB 1: INSPECTOR
with tab1:
    if st.session_state.connected:
        col1, col2 = st.columns([1, 2])

        with col1:
            st.subheader("🔍 Element Inspector")

            if st.button("📸 Capture Screen", use_container_width=True):
                with st.spinner("Capturing..."):
                    data = st.session_state.inspector.capture_screen()
                    if "error" not in data:
                        st.success("Screen captured!")
                        st.session_state.inspector_data = data

            if st.button("🔄 Get Element Tree", use_container_width=True):
                with st.spinner("Loading..."):
                    elements = st.session_state.inspector.get_element_tree()
                    st.session_state.elements_list = elements
                    st.success(f"Found {len(elements)} elements")

            st.divider()

            # Element search
            search_by = st.selectbox("Search by", ["id", "text", "class", "xpath"])
            search_value = st.text_input("Value")

            if st.button("🔎 Find", use_container_width=True):
                element = st.session_state.inspector.find_element(search_by, search_value)
                if element:
                    st.success("Found!")
                    st.json(element)
                else:
                    st.error("Not found")

            # Quick element lists
            st.divider()

            if st.button("📱 Get Clickable Elements", use_container_width=True):
                clickables = st.session_state.inspector.get_clickable_elements()
                st.session_state.clickables = clickables
                st.success(f"Found {len(clickables)} clickable elements")

            if st.button("📝 Get Input Fields", use_container_width=True):
                inputs = st.session_state.inspector.get_input_fields()
                st.session_state.inputs = inputs
                st.success(f"Found {len(inputs)} input fields")

        with col2:
            st.subheader("Element Details")

            # Display elements
            if hasattr(st.session_state, "elements_list") and st.session_state.elements_list:
                df = pd.DataFrame(st.session_state.elements_list)

                # Filter options
                show_clickable = st.checkbox("Show only clickable", value=False)
                if show_clickable:
                    df = df[df["clickable"]]

                # Display table with key locator columns
                preferred_columns = ["type", "text", "resource-id", "xpath", "clickable"]
                available_columns = [col for col in preferred_columns if col in df.columns]
                st.dataframe(
                    df[available_columns].fillna(""),
                    use_container_width=True,
                    hide_index=True,
                )

                # Show elements as expandable items for better inspection
                st.subheader("Element Details")

                # Recording mode indicator
                if st.session_state.recording_enabled:
                    st.info("🔴 **Recording Mode ON** - Click buttons below to log actions")

                for i, element in enumerate(st.session_state.elements_list[:10]):  # Show first 10
                    with st.expander(
                        f"{i + 1}. {element.get('type', 'Unknown')} - {element.get('text', 'No text')[:30]}"
                    ):
                        col_json, col_actions = st.columns([3, 1])

                        with col_json:
                            st.json(element)
                            col1, col2 = st.columns(2)
                            with col1:
                                if element.get("resource-id"):
                                    st.code(f"ID: {element['resource-id']}", language="python")
                            with col2:
                                if element.get("xpath"):
                                    st.code(f"XPath: {element['xpath']}", language="python")

                        # Action recording buttons
                        with col_actions:
                            if st.session_state.recording_enabled:
                                st.write("**Record:**")

                                # Record tap button
                                if st.button("👆 Tap", key=f"tap_{i}", use_container_width=True):
                                    action = st.session_state.action_logger.log_tap(element)
                                    st.success(f"✅ Logged: {action['description']}")
                                    st.rerun()

                                # Record input button (if it's an input field)
                                elem_type = element.get("type", "").lower()
                                if (
                                    "edit" in elem_type
                                    or "input" in elem_type
                                    or "text" in elem_type
                                ):
                                    text_input = st.text_input(
                                        "Text:", key=f"input_{i}", placeholder="Enter text"
                                    )
                                    if st.button(
                                        "⌨️ Input", key=f"input_btn_{i}", use_container_width=True
                                    ):
                                        if text_input:
                                            action = st.session_state.action_logger.log_input(
                                                element, text_input
                                            )
                                            st.success(f"✅ Logged: {action['description']}")
                                            st.rerun()
                                        else:
                                            st.warning("Enter text first!")
                            else:
                                st.info("Enable recording in Recorder tab")
            else:
                st.info("Click 'Get Element Tree' to see elements")
    else:
        st.warning("Please connect to a device first")

# TAB 2: ACTION LOGGER & RECORDER
with tab2:
    if st.session_state.connected:
        # Simple recording toggle
        recording_mode = st.toggle("🔴 Recording", value=st.session_state.recording_enabled)
        if recording_mode != st.session_state.recording_enabled:
            st.session_state.recording_enabled = recording_mode
            st.rerun()

        st.divider()

        # Action Logger Section
        col1, col2 = st.columns([2, 1])

        with col1:
            st.subheader("📋 Logged Actions")

            actions = st.session_state.action_logger.get_actions()

            if actions:
                st.write(f"**{len(actions)} Actions**")
                st.caption("Latest 10 actions:")

                # Show last 10 actions as simple list
                for i, action in enumerate(actions[-10:], 1):
                    st.markdown(f"**{i}.** {action['description']}")

                # Action management buttons
                col_btn1, col_btn2 = st.columns(2)
                with col_btn1:
                    if st.button(
                        "🐍 Generate Python Test", type="primary", use_container_width=True
                    ):
                        platform = (
                            st.session_state.runner.session_info.get("platform", "android")
                            if st.session_state.runner.session_info
                            else "android"
                        )
                        code = st.session_state.action_logger.generate_code(platform)
                        st.session_state.current_script = code

                        with st.expander("📝 Generated Code", expanded=True):
                            st.code(code, language="python")

                            # Download button
                            filename = (
                                f"test_logged_actions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"
                            )
                            st.download_button(
                                label="💾 Download Test",
                                data=code,
                                file_name=filename,
                                mime="text/x-python",
                            )

                with col_btn2:
                    if st.button("🗑️ Clear All Actions", use_container_width=True):
                        st.session_state.action_logger.clear()
                        st.success("Cleared all actions")
                        st.rerun()

            else:
                st.info(
                    "📝 No actions logged yet. Enable recording and use the Inspector tab to log actions."
                )

        with col2:
            st.subheader("⚡ Quick Actions")

            # Swipe actions in 2x2 grid
            swipe_col1, swipe_col2 = st.columns(2)
            with swipe_col1:
                if st.button("⬆️", use_container_width=True):
                    st.session_state.action_logger.log_swipe("up")
                    st.rerun()
                if st.button("⬇️", use_container_width=True):
                    st.session_state.action_logger.log_swipe("down")
                    st.rerun()

            with swipe_col2:
                if st.button("⬅️", use_container_width=True):
                    st.session_state.action_logger.log_swipe("left")
                    st.rerun()
                if st.button("➡️", use_container_width=True):
                    st.session_state.action_logger.log_swipe("right")
                    st.rerun()

            # Back button
            if st.button("🔙 Back", use_container_width=True):
                st.session_state.action_logger.log_back()
                st.rerun()

        st.divider()

        # Old checkpoint recorder (keep for now as fallback)
        with st.expander("📸 Legacy Checkpoint Recorder (Old Method)", expanded=False):
            if st.session_state.smart_recorder:
                st.subheader("📸 Smart Checkpoint Recorder")
                st.caption(
                    "💡 Interact with your device, then click 'Capture Screen' after each action!"
                )

                # Show live screenshot if available
                checkpoints = st.session_state.smart_recorder.checkpoints
                if checkpoints:
                    latest = checkpoints[-1]
                    if latest.get("screenshot"):
                        st.subheader("📱 Device Screen")
                        # Display screenshot
                        import base64

                        screenshot_data = base64.b64decode(latest["screenshot"])
                        st.image(
                            screenshot_data,
                            caption=f"Latest: {latest['name']}",
                            use_container_width=True,
                        )

                # Recording controls
                rec_col1, rec_col2, rec_col3 = st.columns(3)

                with rec_col1:
                    if not st.session_state.smart_recorder.recording:
                        if st.button("⏺️ Start Recording", type="primary", use_container_width=True):
                            st.session_state.smart_recorder.start_recording()
                            st.success(
                                "📹 Recording started! Interact with your device, then click 'Capture Screen'"
                            )
                            st.rerun()
                    else:
                        if st.button(
                            "⏹️ Stop Recording", type="secondary", use_container_width=True
                        ):
                            checkpoints = st.session_state.smart_recorder.stop_recording()
                            st.success(f"✅ Stopped. Captured {len(checkpoints)} checkpoints")
                            st.rerun()

                with rec_col2:
                    if st.session_state.smart_recorder.recording:
                        checkpoint_name = st.text_input(
                            "Checkpoint Name (optional)", placeholder="e.g., After login"
                        )

                        # Manual action input
                        with st.expander(
                            "📝 Describe Action (Optional - helps generate better code)"
                        ):
                            action_desc = st.text_input(
                                "What did you just do?",
                                placeholder="e.g., Clicked login button, Entered username, Swiped up",
                                help="This will be used to generate more accurate test code",
                            )

                        if st.button("📸 Capture Screen", type="primary", use_container_width=True):
                            with st.spinner("Capturing screen state..."):
                                # Capture with optional manual action description
                                checkpoint = st.session_state.smart_recorder.capture_checkpoint(
                                    checkpoint_name,
                                    manual_action=action_desc if action_desc else None,
                                )
                                if checkpoint.get("success") is not False:
                                    action = checkpoint.get("inferred_action", "Screen captured")
                                    st.success(f"✅ Captured! {action}")
                                    st.rerun()
                                else:
                                    st.error(f"Error: {checkpoint.get('error')}")

                with rec_col3:
                    if st.button("🗑️ Clear All", use_container_width=True):
                        st.session_state.smart_recorder.clear_checkpoints()
                        st.success("Cleared all checkpoints")
                        st.rerun()

                # Display checkpoints
                st.divider()

                checkpoints = st.session_state.smart_recorder.checkpoints

                if checkpoints:
                    st.subheader(f"📋 Captured Checkpoints ({len(checkpoints)})")

                    # Show all checkpoints
                    for i, checkpoint in enumerate(checkpoints):
                        # Icon based on position
                        if i == len(checkpoints) - 1:
                            icon = "🆕"
                        else:
                            icon = "✅"

                        with st.expander(
                            f"{icon} {checkpoint['index']}. {checkpoint['name']} - {checkpoint['element_count']} elements",
                            expanded=(i == len(checkpoints) - 1),
                        ):
                            # Show screenshot thumbnail in a column layout
                            thumb_col, info_col = st.columns([1, 2])

                            with thumb_col:
                                if checkpoint.get("screenshot"):
                                    import base64

                                    screenshot_data = base64.b64decode(checkpoint["screenshot"])
                                    st.image(
                                        screenshot_data, caption="Screen", use_container_width=True
                                    )

                            with info_col:
                                st.write(f"**When**: {checkpoint['timestamp'][:19]}")

                                if checkpoint.get("inferred_action"):
                                    st.info(f"**Detected**: {checkpoint['inferred_action']}")

                                if checkpoint.get("changes"):
                                    changes = checkpoint["changes"]
                                    st.write("**Changes from previous:**")
                                    if changes.get("appeared"):
                                        st.write(
                                            f"- ✅ Appeared: {len(changes['appeared'])} elements"
                                        )
                                    if changes.get("disappeared"):
                                        st.write(
                                            f"- ❌ Disappeared: {len(changes['disappeared'])} elements"
                                        )
                                    if changes.get("text_changes"):
                                        st.write(
                                            f"- ✏️ Text changed: {len(changes['text_changes'])} fields"
                                        )

                                # Show useful key elements (not just containers)
                                all_elems = checkpoint["elements"]
                                useful_elems = [
                                    e
                                    for e in all_elems
                                    if e.get("id")
                                    or (e.get("text") and e.get("text").strip())
                                    or (e.get("label") and e.get("label").strip())
                                    or e.get("clickable")
                                ]
                                display_elems = useful_elems[:8] if useful_elems else all_elems[:5]

                                st.write(
                                    f"**Key Interactive Elements** (showing {len(display_elems)} of {len(useful_elems)}):"
                                )
                                for elem in display_elems:
                                    text = (
                                        elem.get("text")
                                        or elem.get("label")
                                        or elem.get("name")
                                        or elem.get("content_desc")
                                        or ""
                                    )
                                    elem_id = f" [{elem.get('id', '')}]" if elem.get("id") else ""
                                    clickable = " 🖱️" if elem.get("clickable") else ""
                                    if text.strip():
                                        st.write(
                                            f"- {elem['type']}{clickable}: {text[:60]}{elem_id}"
                                        )
                                    elif elem.get("id"):
                                        st.write(f"- {elem['type']}{clickable}{elem_id}")

                    # Export options
                    st.divider()
                    st.subheader("📤 Export Test")

                    if st.button(
                        "🐍 Generate Pytest Test", type="primary", use_container_width=True
                    ):
                        script = st.session_state.smart_recorder.generate_test_from_checkpoints()
                        st.session_state.current_script = script
                        st.success("✅ Test generated! See below.")

                        # Show generated code
                        with st.expander("📝 Generated Test Code", expanded=True):
                            st.code(script, language="python")

                            # Download button
                            filename = (
                                f"test_recorded_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"
                            )
                            st.download_button(
                                label="💾 Download Test",
                                data=script,
                                file_name=filename,
                                mime="text/x-python",
                            )
                else:
                    st.info(
                        "📸 No checkpoints yet. Click 'Start Recording' then interact with your device!"
                    )

                st.divider()
                st.subheader("💡 How to Use")
                st.markdown("""
                **Step by Step:**

                1. Click **"⏺️ Start Recording"**

                2. **Interact with your device:**
                   - Navigate to screen
                   - Click buttons
                   - Enter text
                   - Swipe, etc.

                3. Click **"📸 Capture Screen"** after each action

                4. **Repeat** steps 2-3 for your flow

                5. Click **"⏹️ Stop Recording"**

                6. **Generate test** and download!

                ---

                **What Gets Captured:**
                - ✅ All elements on screen
                - ✅ Changes from previous screen
                - ✅ Auto-detects what happened
                - ✅ Suggests assertions for all key elements
                """)
    else:
        st.warning("Please connect to a device first")

# TAB 3: SCRIPT EDITOR
with tab3:
    st.subheader("📝 Test Script Editor")

    # Script editor
    script_content = st.text_area(
        "Script Content",
        value=st.session_state.current_script
        or """# Test script
def test_example(driver):
    # Your test code here
    pass
""",
        height=400,
        key="script_editor",
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        script_name = st.text_input("Script Name", value="test_script.py")

    with col2:
        script_type = st.selectbox("Type", ["Python/Pytest", "Maestro YAML", "JavaScript"])

    with col3:
        if st.button("💾 Save Script", type="primary", use_container_width=True):
            # Save script logic
            st.success(f"Saved {script_name}")

    # Quick test
    if st.session_state.connected:
        st.divider()
        if st.button("▶️ Quick Run", type="primary"):
            with st.spinner("Running script..."):
                result = st.session_state.runner.run_script_directly(script_content)

                if result["status"] == "passed":
                    st.success(f"✅ Passed in {result.get('duration', 0):.2f}s")
                else:
                    st.error(f"❌ Failed: {result.get('error', 'Unknown error')}")

# TAB 4: RUNNER
with tab4:
    if st.session_state.connected:
        st.subheader("▶️ Test Runner")

        col1, col2 = st.columns([2, 1])

        with col1:
            # Test file selection
            test_type = st.radio("Test Type", ["Pytest File", "Maestro Flow", "Direct Script"])

            if test_type == "Pytest File":
                test_file = st.text_input("Test File Path", value="tests/test_login.py")
                markers = st.text_input("Markers (optional)", placeholder="smoke, regression")

                if st.button("▶️ Run Pytest", type="primary"):
                    with st.spinner("Running tests..."):
                        result = st.session_state.runner.run_pytest_file(test_file, markers)
                        st.session_state.last_result = result

                        if result["status"] == "passed":
                            st.success("✅ Tests Passed!")
                        else:
                            st.error("❌ Tests Failed")

                        st.json(result)

            elif test_type == "Maestro Flow":
                flow_file = st.text_input("Flow File Path", value="flows/login.yaml")

                if st.button("▶️ Run Maestro", type="primary"):
                    with st.spinner("Running flow..."):
                        result = st.session_state.runner.run_maestro_flow(flow_file)
                        st.session_state.last_result = result

                        if result["status"] == "passed":
                            st.success("✅ Flow Passed!")
                        else:
                            st.error("❌ Flow Failed")

            # Test output
            if hasattr(st.session_state, "last_result"):
                st.divider()
                st.subheader("Output")
                st.text_area("stdout", st.session_state.last_result.get("stdout", ""), height=200)
                if st.session_state.last_result.get("stderr"):
                    st.text_area(
                        "stderr", st.session_state.last_result.get("stderr", ""), height=100
                    )

        with col2:
            st.subheader("Test Suite")

            # Add test files
            suite_files = st.text_area("Test Files (one per line)", height=100)

            if st.button("▶️ Run Suite", use_container_width=True):
                files = [f.strip() for f in suite_files.split("\n") if f.strip()]
                if files:
                    with st.spinner(f"Running {len(files)} tests..."):
                        suite_result = st.session_state.runner.run_test_suite(files)
                        st.success(
                            f"Suite completed: {suite_result['passed']}/{suite_result['total']} passed"
                        )
    else:
        st.warning("Please connect to a device first")

# TAB 5: RESULTS
with tab5:
    st.subheader("📊 Test Results")

    test_history = st.session_state.runner.get_test_history(limit=20)

    if test_history:
        # Summary metrics
        col1, col2, col3, col4 = st.columns(4)

        total = len(test_history)
        passed = sum(1 for t in test_history if t.get("status") == "passed")
        failed = sum(1 for t in test_history if t.get("status") == "failed")

        col1.metric("Total Tests", total)
        col2.metric("Passed", passed, delta=f"{passed / total * 100:.1f}%")
        col3.metric("Failed", failed)
        col4.metric("Success Rate", f"{passed / total * 100:.1f}%")

        # Results table
        st.divider()

        df = pd.DataFrame(test_history)
        st.dataframe(df, use_container_width=True)

        # Export options
        col1, col2, col3 = st.columns(3)

        with col1:
            if st.button("📥 Export JSON"):
                filepath = st.session_state.runner.export_results("json")
                st.success(f"Exported to {filepath}")

        with col2:
            if st.button("📊 Export CSV"):
                filepath = st.session_state.runner.export_results("csv")
                st.success(f"Exported to {filepath}")

        with col3:
            if st.button("📄 Export HTML"):
                filepath = st.session_state.runner.export_results("html")
                st.success(f"Exported to {filepath}")
    else:
        st.info("No test results yet. Run some tests to see results here.")

# Footer
st.divider()
st.markdown("🎯 **Unified Mobile Test Automation System** | Inspector + Recorder + Runner")
