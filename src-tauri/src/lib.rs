//! Glassfolio desktop shell: native Liquid Glass window around the local web UI,
//! which is served by the Python analysis service running as a child process.

mod auth;
mod keychain;
mod sidecar;

use std::sync::{Arc, Mutex};

use tauri::{Manager, RunEvent, TitleBarStyle, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};
use tauri_plugin_liquid_glass::{GlassMaterialVariant, LiquidGlassConfig, LiquidGlassExt};

#[derive(Default)]
struct Service {
    running: Mutex<Option<sidecar::Running>>,
}

/// Where the webview may go: its own splash page and, once known, the service's origin.
type Allowed = Arc<Mutex<Option<String>>>;

fn settings_require_touch_id() -> bool {
    let home = std::env::var("GLASSFOLIO_HOME").ok().map(std::path::PathBuf::from).or_else(|| {
        std::env::var("HOME").ok().map(|h| std::path::PathBuf::from(h).join("Library/Application Support/Glassfolio"))
    });
    let Some(path) = home.map(|h| h.join("settings.json")) else { return true };
    std::fs::read_to_string(path)
        .ok()
        .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok())
        .and_then(|v| v.get("require_touch_id").and_then(|b| b.as_bool()))
        .unwrap_or(true)
}

fn status(window: &WebviewWindow, text: &str, error: bool) {
    let js = format!("window.glassfolioStatus && window.glassfolioStatus({}, {})",
                     serde_json::to_string(text).unwrap_or_default(), error);
    let _ = window.eval(&js);
}

fn fail(app: &tauri::AppHandle, window: &WebviewWindow, message: &str) {
    status(window, message, true);
    app.dialog().message(message).kind(MessageDialogKind::Error).title("Glassfolio").blocking_show();
    app.exit(1);
}

/// Unlock, open the Keychain, start the service, then show the app.
fn start(app: tauri::AppHandle, window: WebviewWindow, allowed: Allowed) {
    if settings_require_touch_id() {
        status(&window, "Waiting for Touch ID…", false);
        match auth::unlock("unlock your portfolio") {
            Ok(true) => {}
            Ok(false) => return fail(&app, &window, "Glassfolio stays locked."),
            Err(e) => return fail(&app, &window, &e),
        }
    }
    let key = match keychain::load_or_create() {
        Ok(keychain::Key::Existing(key)) => key,
        Ok(keychain::Key::Created(key)) => {
            app.dialog()
                .message(format!(
                    "Glassfolio created an encryption key for your data and stored it in your Keychain.\n\n\
                     Write down this recovery key and keep it offline. Without it, your data can't be \
                     recovered if the Keychain is lost:\n\n{}\n{}",
                    &key[..32], &key[32..]))
                .title("Save your recovery key")
                .kind(MessageDialogKind::Warning)
                .blocking_show();
            key
        }
        Err(e) => return fail(&app, &window, &format!("Couldn't open the Keychain: {e}")),
    };
    status(&window, "Starting the analysis service…", false);
    match sidecar::start(&key) {
        Ok(running) => {
            let url = running.url.clone();
            let origin = sidecar::origin(&url);
            // The service page may drag the window, and nothing else; granted for its exact origin only.
            let capability = tauri::ipc::CapabilityBuilder::new("service-page")
                .remote(format!("{origin}/*"))
                .window("main")
                .permission("core:window:allow-start-dragging");
            if let Err(e) = app.add_capability(capability) {
                eprintln!("couldn't grant window dragging: {e}");
            }
            *allowed.lock().unwrap() = Some(origin);
            *app.state::<Service>().running.lock().unwrap() = Some(running);
            watch(app.clone());
            match url.parse() {
                Ok(parsed) => {
                    let _ = window.navigate(parsed);
                }
                Err(_) => fail(&app, &window, "the service reported an invalid address"),
            }
        }
        Err(e) => fail(&app, &window, &e),
    }
}

/// If the service dies, say so and close, rather than leave a window that talks to nothing
/// (or to whatever takes its port next).
fn watch(app: tauri::AppHandle) {
    std::thread::spawn(move || loop {
        std::thread::sleep(std::time::Duration::from_secs(1));
        let state = app.state::<Service>();
        let mut guard = state.running.lock().unwrap();
        let Some(running) = guard.as_mut() else { return }; // quitting
        if let Ok(Some(status)) = running.child.try_wait() {
            guard.take();
            drop(guard);
            app.dialog()
                .message(format!("Glassfolio's analysis service stopped unexpectedly ({status}). The app will close."))
                .kind(MessageDialogKind::Error)
                .blocking_show();
            app.exit(1);
            return;
        }
    });
}

/// Only the splash page and the local service; nothing else, ever.
fn may_navigate(url: &tauri::Url, service_origin: Option<&str>) -> bool {
    let own = url.scheme() == "tauri";
    let service = service_origin.is_some_and(|o| {
        url.as_str().starts_with(&format!("{o}/")) || url.as_str() == o
    });
    own || service
}

fn apply_glass(app: &tauri::AppHandle, window: &WebviewWindow) {
    let glass = app.liquid_glass();
    if !glass.is_supported() {
        eprintln!("Liquid Glass isn't available on this macOS; using the standard window material");
    }
    let config = LiquidGlassConfig { variant: GlassMaterialVariant::Sidebar, ..Default::default() };
    if let Err(e) = glass.set_effect(window, config) {
        eprintln!("couldn't apply Liquid Glass: {e}");
    }
}

pub fn run() {
    let allowed: Allowed = Arc::new(Mutex::new(None));
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_liquid_glass::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(Service::default())
        .setup({
            let allowed = allowed.clone();
            move |app| {
                let gate = allowed.clone();
                let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                    .title("Glassfolio")
                    .inner_size(1280.0, 820.0)
                    .min_inner_size(900.0, 600.0)
                    .transparent(true)
                    .title_bar_style(TitleBarStyle::Overlay)
                    .hidden_title(true)
                    .traffic_light_position(tauri::LogicalPosition::new(24.0, 28.0))
                    .on_navigation(move |url| may_navigate(url, gate.lock().unwrap().as_deref()))
                    .build()?;
                apply_glass(app.handle(), &window);
                let handle = app.handle().clone();
                std::thread::spawn(move || start(handle, window, allowed));
                Ok(())
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Glassfolio");
    app.run(|app, event| {
        if let RunEvent::Exit = event {
            if let Some(running) = app.state::<Service>().running.lock().unwrap().take() {
                let sidecar::Running { mut child, stdin, .. } = running;
                drop(stdin); // the service exits when its stdin closes
                let deadline = std::time::Instant::now() + std::time::Duration::from_secs(3);
                while std::time::Instant::now() < deadline {
                    if let Ok(Some(_)) = child.try_wait() {
                        return;
                    }
                    std::thread::sleep(std::time::Duration::from_millis(50));
                }
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::may_navigate;

    fn url(s: &str) -> tauri::Url {
        s.parse().unwrap()
    }

    #[test]
    fn navigation_is_locked_to_the_service() {
        let o = Some("http://127.0.0.1:5123");
        assert!(may_navigate(&url("tauri://localhost/index.html"), o));
        assert!(!may_navigate(&url("http://tauri.localhost:9999/"), o));
        assert!(may_navigate(&url("http://127.0.0.1:5123/?token=x"), o));
        assert!(!may_navigate(&url("http://127.0.0.1:5124/"), o));
        assert!(!may_navigate(&url("http://127.0.0.1:51234/"), o));
        assert!(!may_navigate(&url("https://example.com/"), o));
        assert!(!may_navigate(&url("http://127.0.0.1:5123@evil.com/"), o));
        assert!(!may_navigate(&url("http://127.0.0.1:5123/"), None));
    }
}
