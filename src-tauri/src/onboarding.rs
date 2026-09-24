//! First-run recovery-key onboarding, and "Show Recovery Key…" later.
//!
//! The key is shown only in the app's own local pages (tauri://, no network), never in
//! the service's web UI.

use std::sync::mpsc::{self, Sender};
use std::sync::{Condvar, Mutex};
use std::time::Duration;

use tauri::webview::PageLoadEvent;
use tauri::{Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

/// Set once the main window's local page has loaded, so messages to it aren't lost.
#[derive(Default)]
pub struct PageReady {
    loaded: Mutex<bool>,
    changed: Condvar,
}

impl PageReady {
    pub fn mark(&self) {
        *self.loaded.lock().unwrap() = true;
        self.changed.notify_all();
    }

    pub fn wait(&self) {
        let guard = self.loaded.lock().unwrap();
        let _ = self.changed.wait_timeout_while(guard, Duration::from_secs(15), |loaded| !*loaded);
    }
}

#[derive(Default)]
pub struct Onboarding {
    done: Mutex<Option<Sender<()>>>,
}

/// Called by the onboarding page once the person proved they saved the key.
#[tauri::command]
pub fn finish_onboarding(state: tauri::State<'_, Onboarding>) {
    if let Some(done) = state.done.lock().unwrap().take() {
        let _ = done.send(());
    }
}

/// Print the recovery sheet (paper is the best offline copy).
#[tauri::command]
pub fn print_page(window: WebviewWindow) -> Result<(), String> {
    window.print().map_err(|e| e.to_string())
}

fn js_string(text: &str) -> String {
    serde_json::to_string(text).unwrap_or_else(|_| "\"\"".into())
}

/// Walk the person through saving the new key; returns once they've confirmed a copy.
pub fn run(app: &tauri::AppHandle, window: &WebviewWindow, key: &str) {
    let (tx, rx) = mpsc::channel();
    *app.state::<Onboarding>().done.lock().unwrap() = Some(tx);
    let _ = window.eval(format!("window.glassfolioOnboard({})", js_string(key)));
    let _ = rx.recv(); // closing the window quits the app, which ends this thread too
}

/// App menu → Show Recovery Key…: Touch ID (always), then a small window with the key.
pub fn show(app: tauri::AppHandle, key: String) -> tauri::Result<()> {
    if let Some(existing) = app.get_webview_window("recovery") {
        return existing.set_focus();
    }
    let script = format!("window.glassfolioShowKey({})", js_string(&key));
    let window = WebviewWindowBuilder::new(&app, "recovery", WebviewUrl::App("recovery.html".into()))
        .title("Recovery Key")
        .inner_size(560.0, 620.0)
        .resizable(false)
        .transparent(true)
        .title_bar_style(tauri::TitleBarStyle::Overlay)
        .hidden_title(true)
        .on_navigation(|url| url.scheme() == "tauri")
        .on_page_load(move |w, payload| {
            if payload.event() == PageLoadEvent::Finished {
                let _ = w.eval(&script);
            }
        })
        .build()?;
    crate::apply_glass(&app, &window);
    Ok(())
}
