# Unified Mobile Test Automation System

A complete mobile testing solution that integrates Inspector, Recorder, Runner, and Dashboard into a single system.

## 🎯 Architecture

The system is built with clean, modular classes (each under 500 lines):

```
dashboard.py      # Main Streamlit UI - integrates all components
├── inspector.py  # Element inspection and hierarchy analysis
├── recorder.py   # Action recording and script generation  
└── runner.py     # Test execution and session management (DriverFactory-powered)
```

The refreshed runner now delegates session creation to the shared `DriverFactory`/
`AppiumClient` combo from `src/platform`, so dashboard/CLI experiments reuse the same
capability handling as production tests.

## 🚀 Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt --break-system-packages
```

### 2. Start Appium Server
```bash
appium --relaxed-security
```

### 3. Launch Dashboard
```bash
streamlit run src/appium_utils/dashboard.py
```

> Run the command from the repository root so the `src` package is importable.

## 📱 Features

### 🔍 Inspector
- **Element Hierarchy**: View complete UI tree
- **Element Search**: Find by ID, text, class, or XPath
- **Quick Lists**: Get all clickable elements or input fields
- **Properties**: View detailed element attributes
- **Export**: Save hierarchy as JSON/CSV

### 🎬 Recorder  
- **Auto Recording**: Captures all user interactions
- **Manual Actions**: Add custom actions (click, type, swipe, wait)
- **Multi-Format Export**: Convert to Pytest, Maestro, or JavaScript
- **Action Editing**: Modify or delete recorded actions
- **Replay**: Play back recorded actions

### 📝 Script Editor
- **Syntax Highlighting**: Python/YAML/JavaScript support
- **Quick Run**: Execute scripts directly from editor
- **Save/Load**: Manage test script library
- **Auto-Complete**: Helper snippets for common actions

### ▶️ Runner
- **Multi-Platform**: Android and iOS support
- **Test Types**: Pytest files, Maestro flows, or direct scripts
- **Test Suites**: Run multiple tests sequentially
- **Real-time Output**: See stdout/stderr during execution
- **Session Management**: Maintain persistent connections

### 📊 Results
- **Metrics Dashboard**: Pass/fail rates, duration stats
- **History Tracking**: View last 20 test executions
- **Export Options**: JSON, CSV, or HTML reports
- **Filtering**: Sort and filter results

## 💻 Usage Examples

### Basic Workflow

1. **Connect to Device**
   - Select platform (Android/iOS)
   - Enter device ID
   - Specify app package/bundle
   - Click Connect

2. **Inspect Elements**
   - Go to Inspector tab
   - Click "Get Element Tree"
   - Select elements from table
   - Copy XPath or ID for use in scripts

3. **Record Actions**
   - Go to Recorder tab
   - Click "Start Recording"
   - Perform actions on device
   - Stop recording
   - Convert to desired format

4. **Run Tests**
   - Go to Runner tab
   - Select test type
   - Specify file path
   - Click Run
   - View results in Results tab

## 🔧 API Usage

You can also use the classes programmatically:

```python
from appium_utils import ActionRecorder, MobileInspector, TestRunner

# Initialize runner and connect (DriverFactory + AppiumClient under the hood)
runner = TestRunner()
success, msg = runner.connect_android("emulator-5554", "com.example")

if not success:
    raise SystemExit(msg)

# Inspect elements
inspector = MobileInspector(runner.driver)
elements = inspector.get_element_tree()

# Record actions
recorder = ActionRecorder(runner.driver)
recorder.start_recording()
# ... perform actions ...
actions = recorder.stop_recording()
script = recorder.convert_to_pytest()

# Run tests
result = runner.run_script_directly(script)
print(result)
```

> ⚠️ `run_script_directly` executes arbitrary Python inside the active Appium
> session. Only feed it trusted code that you control.

## 📂 File Structure

```
unified-automation/
├── dashboard.py         # Main Streamlit UI
├── inspector.py         # Element inspection class
├── recorder.py          # Action recording class  
├── runner.py           # Test execution class
├── requirements.txt    # Python dependencies
└── README.md          # This file
```

## ⚙️ Configuration

Default settings can be modified in each class:

- **Inspector**: Element limit (30), hierarchy depth
- **Recorder**: Monitoring interval (500ms), action detection
- **Runner**: Timeout values, session settings

## 🐛 Troubleshooting

### Connection Issues
- Ensure Appium server is running
- Check device is connected (`adb devices`)
- Verify app is installed on device

### Element Not Found
- Increase wait timeout
- Check element is visible on screen
- Try different locator strategy

### Recording Issues
- Ensure driver is connected
- Check recording thread is active
- Verify page source changes are detectable

## 📝 Notes

- Each class is independently testable
- Classes communicate through driver instance
- Session state maintained in Streamlit
- All exports saved to current directory

That's it! Clean, modular, and integrated - exactly what you asked for.
