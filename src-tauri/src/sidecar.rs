//! The Python analysis service: started with the key on stdin, stopped on quit.

use std::io::{BufRead, BufReader, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant};

pub const READY: &str = "GLASSFOLIO_READY ";
const START_TIMEOUT: Duration = Duration::from_secs(90);

/// Environment the service may see; everything else (e.g. DYLD_*, GLASSFOLIO_STATIC) is dropped.
const PASS_ENV: [&str; 8] = ["HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "PATH", "GLASSFOLIO_HOME"];

fn verify_bundled(path: &std::path::Path) -> Result<(), String> {
    use sha2::{Digest, Sha256};
    // black_box keeps the constant intact in the binary, so the build can be checked.
    let expected: &str = std::hint::black_box(env!("GLASSFOLIO_SIDECAR_SHA256"));
    let bytes = std::fs::read(path).map_err(|e| format!("the analysis service is missing: {e}"))?;
    let actual = format!("{:x}", Sha256::digest(bytes));
    if expected.is_empty() || actual != expected {
        return Err("the bundled analysis service doesn't match this app; reinstall Glassfolio".into());
    }
    Ok(())
}

fn command() -> Result<Command, String> {
    let args = ["serve", "--port", "0", "--no-browser", "--key-stdin", "--shell", "tauri"];
    if cfg!(debug_assertions) {
        // Development: run the service from this repository with uv.
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
        let uv = std::env::var("HOME")
            .map(|h| PathBuf::from(h).join(".local/bin/uv"))
            .ok()
            .filter(|p| p.exists())
            .unwrap_or_else(|| PathBuf::from("uv"));
        let mut cmd = Command::new(uv);
        cmd.arg("run").arg("--project").arg(repo).arg("--quiet").arg("glassfolio").args(args);
        Ok(cmd)
    } else {
        // The bundled service sits next to the app's executable; the DuckLake extension
        // ships in Contents/Resources/extensions.
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        let service = exe.with_file_name("glassfolio-server");
        verify_bundled(&service)?;
        let mut cmd = Command::new(service);
        if let Some(resources) = exe.parent().and_then(|p| p.parent()).map(|c| c.join("Resources/extensions")) {
            cmd.env("GLASSFOLIO_EXTENSIONS", resources);
        }
        cmd.args(args);
        Ok(cmd)
    }
}

/// A running service. Its stdin stays open for the app's lifetime: the service exits
/// when it closes, so it can't outlive a crashed app.
pub struct Running {
    pub child: Child,
    pub stdin: ChildStdin,
    pub url: String,
}

/// Start the service and return it once it accepts connections.
pub fn start(key: &str) -> Result<Running, String> {
    let mut cmd = command()?;
    cmd.env_clear();
    for name in PASS_ENV {
        if let Ok(value) = std::env::var(name) {
            cmd.env(name, value);
        }
    }
    if cfg!(debug_assertions) {
        if let Ok(value) = std::env::var("PATH") {
            cmd.env("PATH", value); // uv lives on the developer's PATH
        }
    }
    let mut child = cmd
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("couldn't start the analysis service: {e}"))?;
    let mut stdin = child.stdin.take().ok_or("no stdin")?;
    stdin.write_all(format!("{key}\n").as_bytes()).map_err(|e| e.to_string())?;
    stdin.flush().map_err(|e| e.to_string())?;
    let stdout = child.stdout.take().ok_or("no stdout")?;
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Some(url) = line.strip_prefix(READY).and_then(valid_service_url) {
                let _ = tx.send(url);
            } else {
                eprintln!("[service] {line}");
            }
        }
    });
    let url = match rx.recv_timeout(START_TIMEOUT) {
        Ok(url) => url,
        Err(_) => {
            let _ = child.kill();
            return Err("the analysis service didn't start (see the terminal for details)".into());
        }
    };
    wait_for_port(&url)?;
    Ok(Running { child, stdin, url })
}

fn wait_for_port(url: &str) -> Result<(), String> {
    let host = url.trim_start_matches("http://").split('/').next().unwrap_or_default().to_string();
    let deadline = Instant::now() + Duration::from_secs(30);
    while Instant::now() < deadline {
        if TcpStream::connect(&host).is_ok() {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    Err("the analysis service didn't open its port".into())
}

/// Accept only http://127.0.0.1:<port>/?token=… — nothing else may be opened in the window.
pub fn valid_service_url(text: &str) -> Option<String> {
    let url: tauri::Url = text.trim().parse().ok()?;
    let ok = url.scheme() == "http"
        && url.host_str() == Some("127.0.0.1")
        && url.port().is_some()
        && url.path() == "/"
        && url.username().is_empty()
        && url.password().is_none()
        && url.query().is_some_and(|q| q.starts_with("token=") && !q.contains('&'));
    ok.then(|| url.to_string())
}

/// Origin (scheme://host:port) of the service URL, for the navigation lock.
pub fn origin(url: &str) -> String {
    let rest = url.trim_start_matches("http://");
    format!("http://{}", rest.split('/').next().unwrap_or_default())
}

#[cfg(test)]
mod tests {
    use super::{origin, valid_service_url};

    #[test]
    fn only_a_local_token_url_is_accepted() {
        assert!(valid_service_url("http://127.0.0.1:5123/?token=abc").is_some());
        for bad in ["https://127.0.0.1:5123/?token=a", "http://localhost:5123/?token=a", "http://127.0.0.1/?token=a",
                    "http://127.0.0.1:5123/x?token=a", "http://u@127.0.0.1:5123/?token=a",
                    "http://127.0.0.1:5123/?token=a&next=http://evil", "http://evil.com:5123/?token=a", "garbage"] {
            assert!(valid_service_url(bad).is_none(), "{bad}");
        }
    }

    #[test]
    fn origin_is_scheme_host_and_port() {
        assert_eq!(origin("http://127.0.0.1:5123/?token=abc"), "http://127.0.0.1:5123");
    }
}
