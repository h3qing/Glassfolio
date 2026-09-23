//! Touch ID (or the Mac's password) before the key is read.
//!
//! This is an unlock prompt enforced by the app, not hardware binding of the key:
//! binding the Keychain item to biometrics needs a Developer ID–signed app.

#[cfg(target_os = "macos")]
pub fn unlock(reason: &str) -> Result<bool, String> {
    use block2::RcBlock;
    use objc2::runtime::Bool;
    use objc2_foundation::{NSError, NSString};
    use objc2_local_authentication::{LAContext, LAPolicy};
    use std::sync::mpsc;
    use std::time::Duration;

    let context = unsafe { LAContext::new() };
    let policy = LAPolicy::DeviceOwnerAuthentication; // Touch ID, falling back to the password
    if let Err(error) = unsafe { context.canEvaluatePolicy_error(policy) } {
        // Only a Mac without any password has nothing to ask (LAErrorPasscodeNotSet = -5).
        return if error.code() == -5 {
            Ok(true)
        } else {
            Err(format!("Touch ID and password unlock aren't available right now (error {})", error.code()))
        };
    }
    let (tx, rx) = mpsc::channel();
    let reply = RcBlock::new(move |ok: Bool, _error: *mut NSError| {
        let _ = tx.send(ok.as_bool());
    });
    let reason = NSString::from_str(reason);
    unsafe { context.evaluatePolicy_localizedReason_reply(policy, &reason, &reply) };
    rx.recv_timeout(Duration::from_secs(300)).map_err(|_| "the unlock prompt timed out".to_string())
}

#[cfg(not(target_os = "macos"))]
pub fn unlock(_reason: &str) -> Result<bool, String> {
    Ok(true)
}
