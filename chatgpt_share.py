"""Share a sanitized diagnostic report with a user-selected Android app.

This intentionally does not authenticate to ChatGPT or access a ChatGPT session.
The user chooses ChatGPT in Android's share sheet and confirms the send action.
"""
def share_diagnostics_to_chatgpt(report_text):
    report = str(report_text or "").strip()
    if not report:
        raise ValueError("Diagnostic report is empty")

    try:
        from jnius import autoclass
    except Exception as exc:
        raise RuntimeError("Android sharing bridge is unavailable: {}".format(
            type(exc).__name__
        ))

    PythonActivity = autoclass("org.kivy.android.PythonActivity")
    Intent = autoclass("android.content.Intent")
    JavaString = autoclass("java.lang.String")
    activity = PythonActivity.mActivity
    if activity is None:
        raise RuntimeError("Android activity is not available")

    intent = Intent(Intent.ACTION_SEND)
    intent.setType("text/plain")
    intent.putExtra(Intent.EXTRA_TEXT, JavaString(report))
    chooser = Intent.createChooser(
        intent, JavaString("ارسال گزارش عیب‌یابی به ChatGPT")
    )
    activity.startActivity(chooser)
    return True
