use std::{fs::File, path::Path};

#[tauri::command]
fn save_studio_asset(asset_id: String, suggested_name: String) -> Result<Option<String>, String> {
    if asset_id.len() != 36
        || !asset_id
            .chars()
            .all(|value| value.is_ascii_hexdigit() || value == '-')
    {
        return Err("Invalid Studio asset identifier".into());
    }
    let safe_name = Path::new(&suggested_name)
        .file_name()
        .and_then(|value| value.to_str())
        .filter(|value| !value.is_empty())
        .unwrap_or("thikra-export.mp4");
    let Some(destination) = rfd::FileDialog::new()
        .set_title("Save Thikra Studio export")
        .set_file_name(safe_name)
        .add_filter("MP4 video", &["mp4"])
        .save_file()
    else {
        return Ok(None);
    };
    let url = format!("http://127.0.0.1:43192/studio/assets/{asset_id}/download");
    let mut response = reqwest::blocking::Client::new()
        .get(url)
        .send()
        .map_err(|error| format!("Could not reach the local Studio API: {error}"))?
        .error_for_status()
        .map_err(|error| format!("Could not download the Studio asset: {error}"))?;
    let mut output = File::create(&destination)
        .map_err(|error| format!("Could not create the selected file: {error}"))?;
    response
        .copy_to(&mut output)
        .map_err(|error| format!("Could not save the Studio asset: {error}"))?;
    Ok(Some(destination.to_string_lossy().to_string()))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .invoke_handler(tauri::generate_handler![save_studio_asset])
        .run(tauri::generate_context!())
        .expect("error while running Thikra Studio");
}
