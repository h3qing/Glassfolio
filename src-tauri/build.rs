use sha2::{Digest, Sha256};

/// Embed the SHA-256 of the bundled analysis service, so a release app refuses to
/// hand the key to a binary that was swapped after the build.
fn main() {
    let target = std::env::var("TARGET").unwrap_or_default();
    let sidecar = format!("binaries/glassfolio-server-{target}");
    println!("cargo:rerun-if-changed={sidecar}");
    let digest = std::fs::read(&sidecar)
        .map(|bytes| format!("{:x}", Sha256::digest(bytes)))
        .unwrap_or_default();
    println!("cargo:rustc-env=GLASSFOLIO_SIDECAR_SHA256={digest}");
    // App commands: callable only where a capability allows them (the local pages).
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["finish_onboarding", "print_page"]),
    ))
    .expect("tauri build");
}
