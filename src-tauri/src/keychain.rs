//! The database key in the macOS Keychain: the same item the CLI uses
//! (service "glassfolio", account "db-key"), so both open the same lake.

const SERVICE: &str = "glassfolio";
const ACCOUNT: &str = "db-key";

pub enum Key {
    Existing(String),
    /// Created on this launch; the person must save it offline as the recovery key.
    Created(String),
}

fn valid(key: &str) -> bool {
    key.len() == 64 && key.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

const RESTORE_HINT: &str = "Your encrypted data is here, but its key isn't in the Keychain. Restore it with \
your recovery key: in Terminal, run\n/Applications/Glassfolio.app/Contents/MacOS/glassfolio-server key restore";

fn data_exists() -> bool {
    let home = std::env::var("GLASSFOLIO_HOME").ok().map(std::path::PathBuf::from).or_else(|| {
        std::env::var("HOME").ok().map(|h| std::path::PathBuf::from(h).join("Library/Application Support/Glassfolio"))
    });
    home.is_some_and(|h| h.join("catalog.duckdb").exists())
}

/// Add the key; fails if an item already exists (e.g. the CLI created one meanwhile),
/// so an existing key is never overwritten.
#[cfg(target_os = "macos")]
fn add_only(key: &str) -> Result<(), String> {
    use core_foundation::data::CFData;
    use security_framework::item::{ItemAddOptions, ItemAddValue, ItemClass};

    let mut options = ItemAddOptions::new(ItemAddValue::Data {
        class: ItemClass::generic_password(),
        data: CFData::from_buffer(key.as_bytes()),
    });
    options.set_service(SERVICE).set_account_name(ACCOUNT).set_label("Glassfolio database key");
    options.add().map_err(|e| match e.code() {
        -25299 => "another Glassfolio key appeared in the Keychain meanwhile; open Glassfolio again".to_string(),
        _ => format!("Keychain: {e}"),
    })
}

#[cfg(target_os = "macos")]
pub fn load_or_create() -> Result<Key, String> {
    load_or_create_inner(true)
}

#[cfg(target_os = "macos")]
fn load_or_create_inner(create: bool) -> Result<Key, String> {
    use security_framework::passwords::get_generic_password;

    // Development builds may run on synthetic data with a throwaway key (like the CLI's tests).
    if cfg!(debug_assertions) {
        if let Ok(key) = std::env::var("GLASSFOLIO_DB_KEY") {
            return if valid(&key) { Ok(Key::Existing(key)) } else { Err("GLASSFOLIO_DB_KEY is malformed".into()) };
        }
    }

    match get_generic_password(SERVICE, ACCOUNT) {
        Ok(bytes) => {
            let key = String::from_utf8(bytes).map_err(|_| "the stored key is not text".to_string())?;
            if valid(&key) {
                Ok(Key::Existing(key))
            } else {
                Err("the key in the Keychain is malformed".into())
            }
        }
        Err(err) if err.code() == -25300 => {
            // errSecItemNotFound. If encrypted data exists, a new key would lock it away for good.
            if !create {
                return Err("there's no Glassfolio key in the Keychain".into());
            }
            if data_exists() {
                return Err(RESTORE_HINT.into());
            }
            let mut bytes = [0u8; 32];
            getrandom::fill(&mut bytes).map_err(|e| format!("no randomness: {e}"))?;
            let key: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
            add_only(&key)?;
            Ok(Key::Created(key))
        }
        Err(err) => Err(format!("Keychain: {err}")),
    }
}

/// The existing key only (for "Show Recovery Key…"); never creates one.
pub fn load_existing() -> Result<String, String> {
    match load_or_create_inner(false)? {
        Key::Existing(key) | Key::Created(key) => Ok(key),
    }
}

#[cfg(not(target_os = "macos"))]
pub fn load_or_create() -> Result<Key, String> {
    Err("Glassfolio's desktop app currently supports macOS only".into())
}

#[cfg(test)]
mod tests {
    use super::valid;

    #[test]
    fn keys_are_64_lowercase_hex() {
        assert!(valid(&"0".repeat(63).chars().chain("a".chars()).collect::<String>()));
        assert!(!valid("short"));
        assert!(!valid(&"G".repeat(64)));
        assert!(!valid(&"A".repeat(64)));
    }
}
