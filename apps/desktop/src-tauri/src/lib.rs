use serde::Serialize;
use std::{
    fs::{self, File, OpenOptions},
    net::TcpListener,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
};
use tauri::{AppHandle, Manager, RunEvent, State, WebviewWindow};

#[cfg(windows)]
use std::os::windows::{io::AsRawHandle, process::CommandExt};
#[cfg(windows)]
use windows_sys::Win32::{
    Foundation::{CloseHandle, HANDLE},
    System::{
        JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
            SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        },
        Threading::CREATE_NO_WINDOW,
    },
};

const DEVELOPMENT_API: &str = "http://127.0.0.1:43192";

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct DesktopRuntimeInfo {
    api_base_url: String,
    status: String,
    managed: bool,
    log_path: String,
    diagnostic: String,
}

struct RuntimeState {
    manager: Mutex<RuntimeManager>,
}

struct RuntimeManager {
    child: Option<Child>,
    api_executable: Option<PathBuf>,
    runtime_root: PathBuf,
    data_root: PathBuf,
    log_path: PathBuf,
    api_base_url: String,
    status: String,
    diagnostic: String,
    restart_count: u8,
    #[cfg(windows)]
    job: Option<isize>,
}

impl RuntimeManager {
    fn new(app: &AppHandle) -> Result<Self, String> {
        let resource_root = app
            .path()
            .resource_dir()
            .map_err(|error| format!("Could not resolve application resources: {error}"))?
            .join("runtime");
        let data_root = app
            .path()
            .app_data_dir()
            .map_err(|error| format!("Could not resolve application data directory: {error}"))?;
        for name in ["database", "assets", "proxies", "cache", "logs"] {
            fs::create_dir_all(data_root.join(name))
                .map_err(|error| format!("Could not initialize {name}: {error}"))?;
        }
        let api = resource_root.join("api").join("thikra-api.exe");
        let managed = api.is_file();
        let mut manager = Self {
            child: None,
            api_executable: managed.then_some(api),
            runtime_root: resource_root,
            data_root: data_root.clone(),
            log_path: data_root.join("logs").join("engine.jsonl"),
            api_base_url: DEVELOPMENT_API.into(),
            status: if managed { "starting" } else { "development" }.into(),
            diagnostic: String::new(),
            restart_count: 0,
            #[cfg(windows)]
            job: None,
        };
        if managed {
            manager.start()?;
        }
        Ok(manager)
    }

    fn start(&mut self) -> Result<(), String> {
        let Some(executable) = self.api_executable.clone() else {
            self.status = "development".into();
            self.api_base_url = DEVELOPMENT_API.into();
            return Ok(());
        };
        self.stop();
        let port = TcpListener::bind(("127.0.0.1", 0))
            .and_then(|listener| listener.local_addr())
            .map_err(|error| format!("Could not reserve a loopback port: {error}"))?
            .port();
        let database = self.data_root.join("database").join("thikra.db");
        let database_url = format!("sqlite:///{}", database.to_string_lossy().replace('\\', "/"));
        let ffmpeg = self.runtime_root.join("ffmpeg");
        let log = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.log_path)
            .map_err(|error| format!("Could not open the engine log: {error}"))?;
        let stderr = log
            .try_clone()
            .map_err(|error| format!("Could not prepare the engine log: {error}"))?;
        let mut command = Command::new(executable);
        command
            .args(["--host", "127.0.0.1", "--port", &port.to_string()])
            .current_dir(&self.data_root)
            .env("THIKRA_DESKTOP", "1")
            .env("APP_MODE", "DEMO")
            .env("DATABASE_URL", database_url)
            .env("THIKRA_DATA_DIR", self.data_root.join("assets"))
            .env("STEP_CACHE_DIR", self.data_root.join("cache"))
            .env("THIKRA_LOG_PATH", &self.log_path)
            .env("THIKRA_FONT_DIR", self.runtime_root.join("fonts"))
            .env("THIKRA_API_BASE_URL", format!("http://127.0.0.1:{port}"))
            .env("API_CORS_ORIGINS", "http://tauri.localhost,tauri://localhost")
            .env("PATH", ffmpeg)
            .stdout(Stdio::from(log))
            .stderr(Stdio::from(stderr));
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);
        let mut child = command
            .spawn()
            .map_err(|error| format!("Could not start the creative engine: {error}"))?;
        #[cfg(windows)]
        {
            let job = create_kill_job(&child).map_err(|error| {
                let _ = child.kill();
                error
            })?;
            self.job = Some(job);
        }
        self.api_base_url = format!("http://127.0.0.1:{port}");
        self.child = Some(child);
        self.status = "starting".into();
        self.diagnostic.clear();
        Ok(())
    }

    fn refresh(&mut self) {
        if self.api_executable.is_none() {
            self.status = "development".into();
            return;
        }
        if let Some(child) = self.child.as_mut() {
            match child.try_wait() {
                Ok(Some(exit)) => {
                    self.child = None;
                    self.status = "failed".into();
                    self.diagnostic = format!("The embedded engine exited with {exit}.");
                    if self.restart_count < 3 {
                        self.restart_count += 1;
                        if let Err(error) = self.start() {
                            self.diagnostic = error;
                            self.status = "failed".into();
                        }
                    }
                    return;
                }
                Err(error) => {
                    self.status = "failed".into();
                    self.diagnostic = format!("Could not inspect the engine: {error}");
                    return;
                }
                Ok(None) => {}
            }
        }
        let ready = reqwest::blocking::Client::new()
            .get(format!("{}/health/ready", self.api_base_url))
            .timeout(std::time::Duration::from_millis(350))
            .send()
            .is_ok_and(|response| response.status().is_success());
        self.status = if ready { "ready" } else { "starting" }.into();
        if ready {
            self.restart_count = 0;
        }
    }

    fn info(&mut self) -> DesktopRuntimeInfo {
        self.refresh();
        DesktopRuntimeInfo {
            api_base_url: self.api_base_url.clone(),
            status: self.status.clone(),
            managed: self.api_executable.is_some(),
            log_path: self.log_path.to_string_lossy().to_string(),
            diagnostic: self.diagnostic.clone(),
        }
    }

    fn stop(&mut self) {
        #[cfg(windows)]
        if let Some(job) = self.job.take() {
            unsafe { CloseHandle(job as HANDLE) };
        }
        if let Some(child) = self.child.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
        self.child = None;
    }
}

impl Drop for RuntimeManager {
    fn drop(&mut self) {
        self.stop();
    }
}

#[cfg(windows)]
fn create_kill_job(child: &Child) -> Result<isize, String> {
    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return Err("Could not create the Windows lifecycle job".into());
        }
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let configured = SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const _,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        let assigned = AssignProcessToJobObject(job, child.as_raw_handle() as HANDLE);
        if configured == 0 || assigned == 0 {
            CloseHandle(job);
            return Err("Could not attach the engine to the Windows lifecycle job".into());
        }
        Ok(job as isize)
    }
}

#[tauri::command]
fn desktop_runtime_info(state: State<'_, RuntimeState>) -> Result<DesktopRuntimeInfo, String> {
    state
        .manager
        .lock()
        .map_err(|_| "The runtime state is unavailable".to_string())
        .map(|mut manager| manager.info())
}

#[tauri::command]
fn restart_desktop_runtime(state: State<'_, RuntimeState>) -> Result<DesktopRuntimeInfo, String> {
    let mut manager = state
        .manager
        .lock()
        .map_err(|_| "The runtime state is unavailable".to_string())?;
    manager.restart_count = 0;
    manager.start()?;
    Ok(manager.info())
}

#[tauri::command]
fn open_runtime_logs(state: State<'_, RuntimeState>) -> Result<(), String> {
    let log_folder = state
        .manager
        .lock()
        .map_err(|_| "The runtime state is unavailable".to_string())?
        .log_path
        .parent()
        .ok_or_else(|| "The log folder is unavailable".to_string())?
        .to_path_buf();
    #[cfg(windows)]
    Command::new("explorer.exe")
        .arg(log_folder)
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .map_err(|error| format!("Could not open the log folder: {error}"))?;
    Ok(())
}

#[tauri::command]
fn save_studio_asset(
    asset_id: String,
    suggested_name: String,
    state: State<'_, RuntimeState>,
) -> Result<Option<String>, String> {
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
        .add_filter("Thikra export", &["mp4", "srt"])
        .save_file()
    else {
        return Ok(None);
    };
    let api = state
        .manager
        .lock()
        .map_err(|_| "The runtime state is unavailable".to_string())?
        .api_base_url
        .clone();
    let url = format!("{api}/studio/assets/{asset_id}/download");
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

fn focus_main(window: &WebviewWindow) {
    let _ = window.show();
    let _ = window.unminimize();
    let _ = window.set_focus();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                focus_main(&window);
            }
        }))
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .setup(|app| {
            let manager = RuntimeManager::new(app.handle())?;
            app.manage(RuntimeState {
                manager: Mutex::new(manager),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            desktop_runtime_info,
            restart_desktop_runtime,
            open_runtime_logs,
            save_studio_asset
        ]);
    let app = builder
        .build(tauri::generate_context!())
        .expect("error while building Thikra Studio");
    app.run(|app, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            if let Some(state) = app.try_state::<RuntimeState>() {
                if let Ok(mut manager) = state.manager.lock() {
                    manager.stop();
                }
            }
        }
    });
}
