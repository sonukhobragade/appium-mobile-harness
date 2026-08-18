# 🚀 Quick Start: Manual Recording Workflow

## New Feature: Pre-configured Device Auto-Connect

You can now launch the dashboard and it will **automatically connect** to your pre-configured device!

---

## 🎯 Option A: Manual Recording Workflow

### Step 1: Launch Dashboard

```bash
streamlit run src/appium_utils/dashboard.py
```

> Execute from the repository root so the `src` package remains importable.

The dashboard will:
- ✅ Load device profiles from `device_config.json`
- ✅ **Auto-connect** to the default device (`android_dev`)
- ✅ Start recording automatically

### Step 2: Perform Actions on Your Device

Once connected, simply:
1. Pick up your physical device (ZA222TCMXL)
2. Interact with the app manually
3. The **Recorder** captures everything automatically!

### Step 3: Go to Recorder Tab

1. Click on **🎬 Recorder** tab
2. Click **"Start Recording"** if not already recording
3. Perform your test flow on the device
4. Click **"Stop Recording"**

### Step 4: Export Test Flow

Choose your format:
- **Pytest** → Ready-to-run Python test
- **Maestro** → YAML flow file
- **JavaScript** → JS test script

Click **Export** and save the file!

---

## 📱 Device Configuration

Your device is pre-configured in `device_config.json`:

```json
{
  "default_device": "android_dev",
  "devices": {
    "android_dev": {
      "name": "Samsung Galaxy (Dev)",
      "platformName": "Android",
      "appium:udid": "ZA222TCMXL",
      "appium:appPackage": "com.example.app",
      "appium:appActivity": ".MainActivity",
      "appium:autoGrantPermissions": true,
      "appium:skipUnlock": true
    }
  }
}
```

### Adding More Devices

Simply add more profiles to the `devices` section:

```json
{
  "devices": {
    "android_dev": { ... },
    "android_tablet": {
      "name": "Samsung Tab",
      "appium:udid": "TABLET_SERIAL",
      ...
    }
  }
}
```

Then select the device from the dropdown in the dashboard!

---

## 🔧 Manual Connection (Optional)

If auto-connect fails or you want to connect manually:

1. The dashboard shows available device profiles
2. Select your device from the dropdown: **"Samsung Galaxy (Dev) (Android) - ZA222TCMXL"**
3. Click **🔌 Connect**

---

## 📊 Complete Example Workflow

**Goal**: Create a test for the login flow

1. **Launch**: `streamlit run src/appium_utils/dashboard.py`
   - ✅ Auto-connects to ZA222TCMXL
   - ✅ Shows "🟢 Connected"

2. **Go to Recorder Tab**
   - Click "Start Recording"

3. **Perform Login on Device**
   - Enter phone number
   - Click Continue
   - Enter OTP
   - Submit

4. **Stop Recording**
   - Click "Stop Recording"
   - See all captured actions

5. **Export to Pytest**
   - Select "Pytest" format
   - Click "Convert to Pytest"
   - Click "Save Script"
   - Save as `test_login_flow.py`

6. **Run the Test** (from Runner tab or CLI)
   ```bash
   pytest test_login_flow.py
   ```

Done! 🎉

---

## ⚙️ Configuration Options

### Auto-Connect Behavior

- **Enabled by default** - connects to `default_device` on startup
- To disable: Remove or comment out the auto-connect section in dashboard.py (lines 42-56)

### Device Selection

- Dropdown shows: `Name (Platform) - UDID`
- Switch devices anytime by:
  1. Disconnecting current device
  2. Selecting new profile
  3. Clicking Connect

### Appium Server

Default: `http://localhost:4723`

To change:
- Edit `appium_url` in `device_config.json`
- Or start Appium on different port: `appium -p 4725`

---

## 🐛 Troubleshooting

### Auto-Connect Fails

**Issue**: "Auto-connect skipped: Connection failed"

**Solutions**:
1. Check Appium server is running:
   ```bash
   appium --relaxed-security
   ```

2. Verify device is connected:
   ```bash
   adb devices
   # Should show: ZA222TCMXL    device
   ```

3. Check app is installed:
   ```bash
   adb -s ZA222TCMXL shell pm list packages | grep example
   ```

### Recorder Not Capturing

**Issue**: Actions not being recorded

**Solutions**:
1. Ensure "Start Recording" is clicked
2. Check recorder status in UI
3. Verify driver is connected (green status)

### Export Fails

**Issue**: Can't export to Pytest/Maestro

**Solutions**:
1. Ensure at least one action is recorded
2. Check actions list is not empty
3. Try different export format

---

## 📚 Related Files

- **Device Config**: `src/appium_utils/device_config.json`
- **Dashboard**: `src/appium_utils/dashboard.py`
- **Runner**: `src/appium_utils/runner.py`
- **Inspector**: `src/appium_utils/inspector.py`
- **Recorder**: `src/appium_utils/recorder.py`

---

## 🎯 Next Steps

1. **Try it out**: Launch the dashboard and see auto-connect in action
2. **Record a flow**: Create your first test by recording
3. **Run tests**: Execute the generated tests
4. **Add more devices**: Expand your device config for CI/CD

Happy Testing! 🚀
