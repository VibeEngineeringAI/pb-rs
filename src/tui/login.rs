//! `pb login`: the inline deployment/credential wizard (pkg/model/login)
//! and the command orchestration after it exits (cmd/login.go).
use crate::{
    ErrorCode,
    config::{ProfileRecord, ProfileStore},
    error::CliError,
    tui::{
        editor::{EchoMode, InputStyles, TextInput},
        input::{Key, KeyCode},
        profile::{InlineModel, RunEnd, adaptive, caught_panic_stdout, run_inline},
        style::{Border, Color, Role, Style},
    },
};
use std::time::Instant;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Step {
    ChooseType,
    EnterUrl,
    ChooseCloudAuth,
    ChooseAuth,
    EnterUsername,
    EnterPassword,
    EnterApiKey,
    EnterProfileName,
    ConfirmReplace,
    Done,
}

/// What the wizard decided; only meaningful when `done`.
#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct LoginResult {
    pub(crate) done: bool,
    pub(crate) name: String,
    pub(crate) profile: ProfileRecord,
    pub(crate) cloud_device_login: bool,
}

/// Effects the reducer cannot perform itself.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Effect {
    None,
    Quit,
    /// config.ReadConfigFromFile at the profile-name step; any read failure
    /// counts as "does not exist". Answer with `name_checked`.
    CheckName(String),
}

pub(crate) struct LoginWizard {
    step: Step,
    type_index: usize,
    cloud_auth_index: usize,
    auth_index: usize,
    replace_index: usize,
    url: TextInput,
    username: TextInput,
    password: TextInput,
    api_key: TextInput,
    profile_name: TextInput,
    server_url: String,
    error: String,
    result: LoginResult,
}

/// pkg/config.LocalParseableURL, the URL placeholder.
const LOCAL_PARSEABLE_URL: &str = "http://localhost:8000";

fn new_input(placeholder: &str, limit: usize) -> TextInput {
    let mut input = TextInput::new();
    input.set_placeholder(placeholder);
    input.set_char_limit(limit);
    input
}

impl Default for LoginWizard {
    fn default() -> Self {
        Self::new()
    }
}

impl LoginWizard {
    pub(crate) fn new() -> Self {
        let mut password = new_input("password", 64);
        password.set_echo(EchoMode::Password('•'));
        let mut api_key = new_input("paste API key here", 512);
        api_key.set_echo(EchoMode::Password('•'));
        let mut profile_name = new_input("e.g. local, staging, prod", 64);
        profile_name.set_value("default");
        Self {
            step: Step::ChooseType,
            type_index: 0,
            cloud_auth_index: 0,
            auth_index: 0,
            replace_index: 0,
            url: new_input(LOCAL_PARSEABLE_URL, 256),
            username: new_input("admin", 64),
            password,
            api_key,
            profile_name,
            server_url: String::new(),
            error: String::new(),
            result: LoginResult::default(),
        }
    }

    pub(crate) fn result(&self) -> &LoginResult {
        &self.result
    }

    fn active_input(&mut self) -> Option<&mut TextInput> {
        match self.step {
            Step::EnterUrl => Some(&mut self.url),
            Step::EnterUsername => Some(&mut self.username),
            Step::EnterPassword => Some(&mut self.password),
            Step::EnterApiKey => Some(&mut self.api_key),
            Step::EnterProfileName => Some(&mut self.profile_name),
            _ => None,
        }
    }

    fn inputs(&self) -> [&TextInput; 5] {
        [
            &self.url,
            &self.username,
            &self.password,
            &self.api_key,
            &self.profile_name,
        ]
    }

    fn inputs_mut(&mut self) -> [&mut TextInput; 5] {
        [
            &mut self.url,
            &mut self.username,
            &mut self.password,
            &mut self.api_key,
            &mut self.profile_name,
        ]
    }

    /// Go `x.Focus()` whose command login discards: the cursor shows, its
    /// tag advances and its outstanding timer is canceled; nothing is armed.
    fn focus_input(input: &mut TextInput) {
        input.focus();
        input.disarm_blink();
    }

    /// The `textinput.Blink` login returns with each focus change. Its
    /// `initialBlinkMsg` is routed like every non-key message, to the input
    /// of the step current at delivery (selection steps drop it). Go sends
    /// it from a command goroutine; delivering it here, before the next key,
    /// is a deterministic ordering assumption, not a Go guarantee.
    fn initial_blink(&mut self, now: Instant) {
        if let Some(input) = self.active_input() {
            input.start_blink_at(now);
        }
    }

    fn up_down(index: &mut usize, key: &Key) -> bool {
        match key.code {
            KeyCode::Up => *index = index.saturating_sub(1),
            KeyCode::Down => *index = (*index + 1).min(1),
            _ => return false,
        }
        true
    }

    /// login.Model.Update for a key message.
    pub(crate) fn update(&mut self, key: &Key) -> Effect {
        self.update_at(key, Instant::now())
    }

    /// `update` delivered at `now`, the instant blink timers it arms start.
    pub(crate) fn update_at(&mut self, key: &Key, now: Instant) -> Effect {
        if key.name() == "ctrl+c" {
            return Effect::Quit;
        }
        let esc = key.code == KeyCode::Esc;
        let enter = key.code == KeyCode::Enter;
        match self.step {
            Step::ChooseType => {
                if !Self::up_down(&mut self.type_index, key) && enter {
                    self.error.clear();
                    if self.type_index == 0 {
                        self.step = Step::EnterUrl;
                        Self::focus_input(&mut self.url);
                        self.initial_blink(now);
                    } else {
                        self.step = Step::ChooseCloudAuth;
                    }
                }
                return Effect::None;
            }
            Step::ChooseCloudAuth => {
                if esc {
                    self.error.clear();
                    self.step = Step::ChooseType;
                } else if !Self::up_down(&mut self.cloud_auth_index, key) && enter {
                    self.error.clear();
                    if self.cloud_auth_index == 0 {
                        self.step = Step::EnterProfileName;
                        Self::focus_input(&mut self.profile_name);
                    } else {
                        self.step = Step::EnterApiKey;
                        Self::focus_input(&mut self.api_key);
                    }
                    self.initial_blink(now);
                }
                return Effect::None;
            }
            Step::EnterUrl if esc => {
                self.error.clear();
                self.step = Step::ChooseType;
                self.url.blur();
                return Effect::None;
            }
            Step::EnterUrl if enter => {
                let value = self.url.value().trim().to_owned();
                if value.is_empty() {
                    self.error = "Server URL is required".into();
                    return Effect::None;
                }
                self.server_url = value;
                self.error.clear();
                self.step = Step::ChooseAuth;
                self.url.blur();
                return Effect::None;
            }
            Step::ChooseAuth => {
                if esc {
                    self.error.clear();
                    self.step = Step::EnterUrl;
                    Self::focus_input(&mut self.url);
                    self.initial_blink(now);
                } else if !Self::up_down(&mut self.auth_index, key) && enter {
                    self.error.clear();
                    if self.auth_index == 0 {
                        self.step = Step::EnterUsername;
                        Self::focus_input(&mut self.username);
                    } else {
                        self.step = Step::EnterApiKey;
                        Self::focus_input(&mut self.api_key);
                    }
                    self.initial_blink(now);
                }
                return Effect::None;
            }
            Step::EnterUsername if esc => {
                self.error.clear();
                self.step = Step::ChooseAuth;
                self.username.blur();
                return Effect::None;
            }
            Step::EnterUsername if enter => {
                if self.username.value().trim().is_empty() {
                    self.error = "Username is required".into();
                    return Effect::None;
                }
                self.error.clear();
                self.step = Step::EnterPassword;
                self.username.blur();
                Self::focus_input(&mut self.password);
                self.initial_blink(now);
                return Effect::None;
            }
            Step::EnterPassword if esc => {
                self.error.clear();
                self.step = Step::EnterUsername;
                self.password.blur();
                Self::focus_input(&mut self.username);
                self.initial_blink(now);
                return Effect::None;
            }
            Step::EnterPassword if enter => {
                // Passwords are not trimmed: whitespace-only is accepted.
                if self.password.value().is_empty() {
                    self.error = "Password is required".into();
                    return Effect::None;
                }
                self.error.clear();
                self.step = Step::EnterProfileName;
                self.password.blur();
                Self::focus_input(&mut self.profile_name);
                self.initial_blink(now);
                return Effect::None;
            }
            Step::EnterApiKey if esc => {
                self.error.clear();
                self.step = if self.type_index == 1 {
                    Step::ChooseCloudAuth
                } else {
                    Step::ChooseAuth
                };
                self.api_key.blur();
                return Effect::None;
            }
            Step::EnterApiKey if enter => {
                if self.api_key.value().trim().is_empty() {
                    self.error = "API key is required".into();
                    return Effect::None;
                }
                self.error.clear();
                self.step = Step::EnterProfileName;
                self.api_key.blur();
                Self::focus_input(&mut self.profile_name);
                self.initial_blink(now);
                return Effect::None;
            }
            Step::EnterProfileName if esc => {
                self.error.clear();
                self.profile_name.blur();
                if self.type_index == 1 {
                    if self.cloud_auth_index == 0 {
                        self.step = Step::ChooseCloudAuth;
                    } else {
                        self.step = Step::EnterApiKey;
                        Self::focus_input(&mut self.api_key);
                    }
                } else if self.auth_index == 0 {
                    self.step = Step::EnterPassword;
                    Self::focus_input(&mut self.password);
                } else {
                    self.step = Step::EnterApiKey;
                    Self::focus_input(&mut self.api_key);
                }
                // Returned on every path; dropped at the cloud auth choice.
                self.initial_blink(now);
                return Effect::None;
            }
            Step::EnterProfileName if enter => {
                let value = self.profile_name.value().trim().to_owned();
                if value.is_empty() {
                    self.error = "Profile name is required".into();
                    return Effect::None;
                }
                self.error.clear();
                return Effect::CheckName(value);
            }
            Step::ConfirmReplace => {
                if esc {
                    self.error.clear();
                    self.step = Step::EnterProfileName;
                    Self::focus_input(&mut self.profile_name);
                    self.initial_blink(now);
                } else if !Self::up_down(&mut self.replace_index, key) && enter {
                    if self.replace_index == 1 {
                        self.error.clear();
                        self.step = Step::EnterProfileName;
                        Self::focus_input(&mut self.profile_name);
                        self.initial_blink(now);
                    } else {
                        let name = self.result.name.clone();
                        return self.finalize(name);
                    }
                }
                return Effect::None;
            }
            _ => {}
        }
        // Character input and every other key go to the active text input.
        if let Some(input) = self.active_input() {
            input.update_at(key, now);
        }
        Effect::None
    }

    /// pasteMsg: forwarded to the input of the step current at delivery;
    /// selection steps drop it.
    pub(crate) fn paste(&mut self, text: &str) {
        self.paste_at(text, Instant::now());
    }

    /// `paste` delivered at `now`.
    pub(crate) fn paste_at(&mut self, text: &str, now: Instant) {
        if let Some(input) = self.active_input() {
            input.insert_paste_at(text, now);
        }
    }

    /// Earliest outstanding blink timer of any field. Go's `Blur` cancels
    /// nothing, so departed fields keep theirs; at most one per field.
    pub(crate) fn next_blink(&self) -> Option<Instant> {
        self.inputs()
            .into_iter()
            .filter_map(TextInput::pending_blink)
            .map(|timer| timer.due)
            .min()
    }

    /// One due timer fires. Its `BlinkMsg` (cursor id 0, the source field's
    /// tag) goes where login.Update sends every non-key message: to the
    /// active step's input, which accepts it only with its own current tag.
    /// A departed field's timer can therefore toggle the field now active
    /// and replace that field's own timer (Go quirk kept); selection steps
    /// drop it. One message per call; timers due together fire in field
    /// order (Go's goroutine order is unspecified).
    pub(crate) fn blink(&mut self, now: Instant) {
        let due = self
            .inputs()
            .into_iter()
            .enumerate()
            .filter_map(|(index, input)| {
                input
                    .pending_blink()
                    .filter(|timer| timer.due <= now)
                    .map(|timer| (timer.due, index))
            })
            .min();
        let Some((_, source)) = due else {
            return;
        };
        let Some(timer) = self
            .inputs_mut()
            .into_iter()
            .nth(source)
            .and_then(TextInput::take_pending_blink)
        else {
            return;
        };
        if let Some(input) = self.active_input() {
            input.deliver_blink(timer.tag, now);
        }
    }

    /// Completion of `Effect::CheckName`.
    pub(crate) fn name_checked(&mut self, name: String, exists: bool) -> Effect {
        if exists {
            self.result.name = name;
            self.replace_index = 0;
            self.step = Step::ConfirmReplace;
            self.profile_name.blur();
            return Effect::None;
        }
        self.finalize(name)
    }

    fn finalize(&mut self, name: String) -> Effect {
        self.result.name = name;
        self.result.profile = if self.type_index == 1 && self.cloud_auth_index == 0 {
            self.result.cloud_device_login = true;
            ProfileRecord {
                cloud: true,
                ..Default::default()
            }
        } else if self.type_index == 1 {
            ProfileRecord {
                cloud: true,
                api_key: self.api_key.value().trim().to_owned(),
                ..Default::default()
            }
        } else if self.auth_index == 0 {
            ProfileRecord {
                url: self.server_url.clone(),
                username: self.username.value().trim().to_owned(),
                password: self.password.value(),
                ..Default::default()
            }
        } else {
            ProfileRecord {
                url: self.server_url.clone(),
                api_key: self.api_key.value().trim().to_owned(),
                ..Default::default()
            }
        };
        self.result.done = true;
        self.step = Step::Done;
        Effect::Quit
    }

    // ── View ────────────────────────────────────────────────────────────

    fn input_styles() -> InputStyles {
        InputStyles {
            prompt: adaptive(Role::Accent),
            text: adaptive(Role::Body),
            cursor: adaptive(Role::Accent),
            ..InputStyles::default()
        }
    }

    fn row(selected: bool, label: &str) -> String {
        if selected {
            format!(
                "{} {}",
                Style::new().bg(Color::role(Role::Active)).render(" "),
                adaptive(Role::Active)
                    .bold(true)
                    .render(&format!("❯ {label}"))
            )
        } else {
            format!("    {}", adaptive(Role::Body).render(label))
        }
    }

    fn hint(pairs: &[(&str, &str)]) -> String {
        let key = adaptive(Role::Accent).bold(true);
        let hint = adaptive(Role::Faint);
        let parts: Vec<String> = pairs
            .iter()
            .map(|(k, action)| key.render(&format!("<{k}>")) + &hint.render(&format!(" {action}")))
            .collect();
        format!("  {}", parts.join(&hint.render("    ")))
    }

    fn render_error(message: &str) -> String {
        if message.is_empty() {
            return String::new();
        }
        adaptive(Role::Err).render(&format!("  ✗ {message}")) + "\n\n"
    }

    fn label(text: &str) -> String {
        adaptive(Role::Mute).bold(true).render(text)
    }

    fn choices(&self, out: &mut String, title: &str, entries: &[&str], index: usize, badge: bool) {
        out.push_str(&Self::label(title));
        out.push_str("\n\n");
        for (i, entry) in entries.iter().enumerate() {
            out.push_str(&Self::row(i == index, entry));
            if badge {
                out.push_str(&adaptive(Role::Faint).render(""));
            }
            out.push('\n');
        }
        out.push('\n');
    }

    fn field(&self, out: &mut String, title: &str, input: &TextInput, enter: &str) {
        out.push_str(&Self::label(title));
        out.push_str("\n\n  ");
        out.push_str(&input.view(&Self::input_styles()));
        out.push_str("\n\n");
        out.push_str(&Self::render_error(&self.error));
        out.push_str(&Self::hint(&[("esc", "back"), ("enter", enter)]));
    }

    /// login.Model.View.
    pub(crate) fn view(&self) -> String {
        if self.step == Step::Done && self.result.cloud_device_login {
            return String::new();
        }
        let mut b = adaptive(Role::Accent).bold(true).render("PARSEABLE LOGIN");
        b.push_str("\n\n");
        let navigate = [("esc", "back"), ("↑↓", "navigate"), ("enter", "select")];
        match self.step {
            Step::ChooseType => {
                self.choices(
                    &mut b,
                    "CONNECT TO",
                    &["Self-hosted", "Parseable Cloud"],
                    self.type_index,
                    true,
                );
                b.push_str(&Self::hint(&[
                    ("↑↓", "navigate"),
                    ("enter", "select"),
                    ("ctrl-c", "quit"),
                ]));
            }
            Step::EnterUrl => self.field(&mut b, "SERVER URL", &self.url, "continue"),
            Step::ChooseCloudAuth => {
                self.choices(
                    &mut b,
                    "CLOUD AUTHENTICATION",
                    &["Device login (browser)", "API key"],
                    self.cloud_auth_index,
                    false,
                );
                b.push_str(&Self::hint(&navigate));
            }
            Step::ChooseAuth => {
                self.choices(
                    &mut b,
                    "AUTHENTICATION",
                    &["Username & Password", "API key"],
                    self.auth_index,
                    false,
                );
                b.push_str(&Self::hint(&navigate));
            }
            Step::EnterUsername => self.field(&mut b, "USERNAME", &self.username, "continue"),
            Step::EnterPassword => self.field(&mut b, "PASSWORD", &self.password, "continue"),
            Step::EnterApiKey => {
                let title = if self.type_index == 1 {
                    "CLOUD API KEY"
                } else {
                    "API KEY"
                };
                self.field(&mut b, title, &self.api_key, "continue");
            }
            Step::EnterProfileName => {
                self.field(&mut b, "PROFILE NAME", &self.profile_name, "save")
            }
            Step::ConfirmReplace => {
                b.push_str(
                    &adaptive(Role::Err)
                        .render(&format!("  Profile '{}' already exists", self.result.name)),
                );
                b.push_str("\n\n");
                for (i, entry) in ["Replace it", "Change name"].iter().enumerate() {
                    b.push_str(&Self::row(i == self.replace_index, entry));
                    b.push('\n');
                }
                b.push('\n');
                b.push_str(&Self::hint(&navigate));
            }
            Step::Done => {
                let profile = &self.result.profile;
                let label = Self::label;
                let normal = |text: &str| adaptive(Role::Body).render(text);
                b.push_str(
                    &adaptive(Role::Ok)
                        .bold(true)
                        .render(&format!("✓ profile '{}' saved", self.result.name)),
                );
                b.push_str("\n\n");
                if !profile.url.is_empty() {
                    b.push_str(&format!("  {}{}\n", label("URL  "), normal(&profile.url)));
                }
                if profile.cloud {
                    b.push_str(&format!(
                        "  {}{}\n",
                        label("AUTH "),
                        normal("Cloud API key (stored after validation)")
                    ));
                }
                if !profile.username.is_empty() {
                    b.push_str(&format!(
                        "  {}{}\n",
                        label("USER "),
                        normal(&profile.username)
                    ));
                }
                if !profile.api_key.is_empty() {
                    b.push_str(&format!(
                        "  {}{}\n",
                        label("AUTH "),
                        normal("API key (stored)")
                    ));
                }
                let dim = adaptive(Role::Faint);
                b.push('\n');
                b.push_str(&dim.render("  Next steps:"));
                b.push_str("\n  ");
                b.push_str(&dim.render("pb status        Verify active connection"));
                b.push_str("\n  ");
                b.push_str(&dim.render("pb dataset list  Explore available datasets"));
            }
        }
        Style::new()
            .border(Border::NORMAL)
            .border_fg(Color::role(Role::Border))
            .padding(&[1, 2])
            .width(60)
            .render(&b)
            + "\n"
    }
}

/// Driver adapter: answers `CheckName` synchronously from the config file.
struct WizardSession<F: FnMut(&str) -> bool> {
    wizard: LoginWizard,
    exists: F,
}

impl<F: FnMut(&str) -> bool> InlineModel for WizardSession<F> {
    fn key(&mut self, key: &Key) -> bool {
        let mut effect = self.wizard.update(key);
        if let Effect::CheckName(name) = effect {
            let exists = (self.exists)(&name);
            effect = self.wizard.name_checked(name, exists);
        }
        effect == Effect::Quit
    }
    /// Every field's outstanding timer, not only the active step's.
    fn next_tick(&self) -> Option<Instant> {
        self.wizard.next_blink()
    }
    fn tick(&mut self, now: Instant) {
        self.wizard.blink(now);
    }
    fn take_paste_request(&mut self) -> bool {
        let mut requested = false;
        for input in self.wizard.inputs_mut() {
            requested |= input.take_paste_request();
        }
        requested
    }
    fn apply_paste(&mut self, text: &str) {
        self.wizard.paste(text);
    }
    /// URL, username and profile-name fields are EchoNormal, so bubbles'
    /// word-delete faults are reachable (L-AUTH-11).
    fn take_panic(&mut self) -> Option<String> {
        let mut first = None;
        for input in self.wizard.inputs_mut() {
            if let Some(fault) = input.take_fault() {
                first.get_or_insert(fault.to_string());
            }
        }
        first
    }
    fn view(&self) -> String {
        self.wizard.view()
    }
}

fn profile_exists(name: &str) -> bool {
    ProfileStore::from_env()
        .and_then(|store| store.read())
        .is_ok_and(|config| config.profiles.contains_key(name))
}

/// config.CloudOrchestratorURL, injected at build time; used untrimmed.
fn cloud_orchestrator_url() -> &'static str {
    option_env!("PB_CLOUD_ORCHESTRATOR_URL").unwrap_or("")
}

/// cmd/login.go LoginCmd.RunE. Returns stdout printed after the terminal
/// has been restored; "" when the wizard is canceled.
pub(crate) fn run() -> Result<String, CliError> {
    let mut session = WizardSession {
        wizard: LoginWizard::new(),
        exists: profile_exists,
    };
    let end = run_inline(&mut session).map_err(|e| CliError::new(ErrorCode::CommandFailed, e))?;
    // LoginCmd asserts with `ok`: a recovered panic (nil model) returns nil,
    // so only recoverFromPanic's stdout remains and the command succeeds.
    if let RunEnd::Panicked(value) = end {
        return Ok(caught_panic_stdout(&value));
    }
    let result = session.wizard.result().clone();
    if !result.done {
        return Ok(String::new());
    }
    complete(result, cloud_orchestrator_url())
}

/// Everything after the wizard: Cloud authentication, then the login save
/// policy (overwrite after any read error, select as default).
fn complete(mut result: LoginResult, orchestrator_url: &str) -> Result<String, CliError> {
    if result.cloud_device_login || result.profile.cloud {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|e| CliError::new(ErrorCode::CommandFailed, e.to_string()))?;
        if result.cloud_device_login {
            result.profile = super::device::login(&runtime, orchestrator_url)?;
            if result.name.is_empty() {
                result.name = super::device::profile_name_from_session(&result.profile);
            }
        } else {
            let api_key = result.profile.api_key.clone();
            let workspace = runtime.block_on(crate::api::validate_cloud_api_key(
                orchestrator_url,
                &api_key,
            ))?;
            result.profile = ProfileRecord {
                url: workspace.url,
                cloud: true,
                api_key,
                tenant_id: workspace.tenant_id,
                workspace_id: workspace.workspace_id,
                workspace_name: workspace.workspace_name,
                orchestrator_url: orchestrator_url.to_owned(),
                ..Default::default()
            };
        }
    }
    let saved = ProfileStore::from_env()
        .map_err(crate::profile::ProfileError::from)
        .and_then(|store| crate::profile::save_login_profile(&store, &result.name, result.profile));
    if let Err(error) = saved {
        return Err(CliError::new(
            error.code,
            format!("failed to save profile: {}", error.message),
        ));
    }
    Ok(if result.cloud_device_login {
        format!(
            "  ✓ Profile '{}' saved.\n\n  💡 Tip: You can add more profiles anytime using:\n     pb profile add <name> <url> [user] [pass]\n",
            result.name
        )
    } else {
        String::new()
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tui::editor::BlinkTimer;
    use crate::tui::profile::tests::{key, typed};
    use std::time::Duration;

    fn drive(wizard: &mut LoginWizard, keys: &[Key], existing: &[&str]) -> Effect {
        let mut last = Effect::None;
        for k in keys {
            last = wizard.update(k);
            if let Effect::CheckName(name) = last {
                let exists = existing.contains(&name.as_str());
                last = wizard.name_checked(name, exists);
            }
            if last == Effect::Quit {
                break;
            }
        }
        last
    }

    fn keys(script: &[&str]) -> Vec<Key> {
        script
            .iter()
            .flat_map(|s| match *s {
                "enter" | "esc" | "up" | "down" | "tab" | "backspace" | "ctrl+c" | "left"
                | "alt+d" => vec![key(s)],
                text => typed(text),
            })
            .collect()
    }

    #[test]
    fn self_hosted_basic_trims_url_and_user_but_not_password() {
        let mut w = LoginWizard::new();
        let script = keys(&[
            "enter",
            " http://h:8000 ",
            "enter",
            "enter",
            " admin ",
            "enter",
            " pw ",
            "enter",
            "enter",
        ]);
        assert_eq!(drive(&mut w, &script, &[]), Effect::Quit);
        let r = w.result();
        assert!(r.done && !r.cloud_device_login);
        assert_eq!(r.name, "default");
        assert_eq!(r.profile.url, "http://h:8000");
        assert_eq!(r.profile.username, "admin");
        assert_eq!(r.profile.password, " pw ");
        assert!(!r.profile.cloud && r.profile.api_key.is_empty());
    }

    #[test]
    fn required_fields_and_backtracking() {
        let mut w = LoginWizard::new();
        drive(&mut w, &keys(&["enter", "   ", "enter"]), &[]);
        assert_eq!(
            (w.step, w.error.as_str()),
            (Step::EnterUrl, "Server URL is required")
        );
        drive(&mut w, &keys(&["x", "enter", "down", "enter"]), &[]);
        assert_eq!(w.step, Step::EnterApiKey);
        drive(&mut w, &keys(&["  ", "enter"]), &[]);
        assert_eq!(w.error, "API key is required");
        // Esc from a self-hosted API key returns to the auth choice, then URL.
        drive(&mut w, &keys(&["esc"]), &[]);
        assert_eq!((w.step, w.error.as_str()), (Step::ChooseAuth, ""));
        drive(&mut w, &keys(&["esc"]), &[]);
        assert_eq!(w.step, Step::EnterUrl);
        assert_eq!(w.url.value(), "   x");
        // Whitespace-only passwords are accepted; empty ones are not.
        let mut w = LoginWizard::new();
        drive(
            &mut w,
            &keys(&["enter", "u", "enter", "enter", "a", "enter", "enter"]),
            &[],
        );
        assert_eq!(w.error, "Password is required");
        drive(&mut w, &keys(&[" ", "enter"]), &[]);
        assert_eq!(w.step, Step::EnterProfileName);
        // Ctrl+C anywhere quits without a result.
        assert_eq!(drive(&mut w, &keys(&["ctrl+c"]), &[]), Effect::Quit);
        assert!(!w.result().done);
    }

    #[test]
    fn duplicate_name_replace_or_change() {
        let mut w = LoginWizard::new();
        let script = keys(&["down", "enter", "down", "enter", "k", "enter", "enter"]);
        assert_eq!(drive(&mut w, &script, &["default"]), Effect::None);
        assert_eq!(w.step, Step::ConfirmReplace);
        // Change name returns to the field with its value intact.
        drive(&mut w, &keys(&["down", "enter"]), &["default"]);
        assert_eq!(
            (w.step, w.profile_name.value()),
            (Step::EnterProfileName, "default".into())
        );
        let script = keys(&["backspace", "backspace", "backspace", "enter"]);
        assert_eq!(drive(&mut w, &script, &["default"]), Effect::Quit);
        let r = w.result();
        assert_eq!(r.name, "defa");
        assert!(r.profile.cloud && r.profile.api_key == "k" && r.profile.url.is_empty());

        // Replace keeps the duplicate name.
        let mut w = LoginWizard::new();
        let script = keys(&["down", "enter", "enter", "enter", "enter"]);
        assert_eq!(drive(&mut w, &script, &["default"]), Effect::Quit);
        assert!(w.result().cloud_device_login && w.result().name == "default");
        assert_eq!(w.view(), "");
    }

    #[test]
    fn alt_d_fault_in_a_wizard_field_is_a_recovered_panic() {
        let mut session = WizardSession {
            wizard: LoginWizard::new(),
            exists: |_: &str| false,
        };
        for k in keys(&["enter", "ab", "left", "alt+d"]) {
            assert!(!session.key(&k));
        }
        assert_eq!(
            session.take_panic().as_deref(),
            Some("runtime error: index out of range [2] with length 2")
        );
        assert_eq!(session.take_panic(), None);
        // Masked fields delete to the end instead and cannot fault.
        let mut session = WizardSession {
            wizard: LoginWizard::new(),
            exists: |_: &str| false,
        };
        for k in keys(&["down", "enter", "down", "enter", "ab", "left", "alt+d"]) {
            session.key(&k);
        }
        assert_eq!(session.take_panic(), None);
        assert_eq!(session.wizard.api_key.value(), "a");
    }

    #[test]
    fn cloud_esc_paths() {
        let mut w = LoginWizard::new();
        drive(&mut w, &keys(&["down", "enter", "enter"]), &[]);
        assert_eq!(w.step, Step::EnterProfileName);
        drive(&mut w, &keys(&["esc"]), &[]);
        assert_eq!(w.step, Step::ChooseCloudAuth);
        drive(&mut w, &keys(&["down", "enter", "esc"]), &[]);
        assert_eq!(w.step, Step::ChooseCloudAuth);
        drive(&mut w, &keys(&["esc", "esc"]), &[]);
        assert_eq!(w.step, Step::ChooseType);
    }

    // ── Blink timers (bubbles cursor tags; pinned Go dwell controls) ──────
    //
    // Tags: login discards each Focus() command (tag +1, nothing armed) and
    // returns textinput.Blink (tag +1, armed); every cursor move and every
    // accepted BlinkMsg arms the next tag. One typed chunk is one key.

    fn at(t0: Instant, ms: u64) -> Instant {
        t0 + Duration::from_millis(ms)
    }

    fn press(w: &mut LoginWizard, name: &str, t0: Instant, ms: u64) {
        assert_eq!(w.update_at(&key(name), at(t0, ms)), Effect::None);
    }

    fn timer(t0: Instant, ms: u64, tag: u64) -> Option<BlinkTimer> {
        Some(BlinkTimer {
            due: at(t0, ms),
            tag,
        })
    }

    fn outstanding(w: &LoginWizard) -> usize {
        w.inputs()
            .into_iter()
            .filter(|input| input.pending_blink().is_some())
            .count()
    }

    /// Self-hosted basic auth: the URL is typed and left at t0 and its timer
    /// fires at the auth choice; the username is focused at +600 ms and one
    /// chunk typed at +610 ms.
    fn username_typed(t0: Instant) -> LoginWizard {
        let mut w = LoginWizard::new();
        press(&mut w, "enter", t0, 0);
        press(&mut w, "http://h:8000", t0, 0);
        press(&mut w, "enter", t0, 0);
        assert_eq!(w.step, Step::ChooseAuth);
        assert_eq!(w.url.pending_blink(), timer(t0, 530, 3));
        w.blink(at(t0, 530));
        assert_eq!(
            w.next_blink(),
            None,
            "a selection step consumes and drops it"
        );
        assert!(!w.url.cursor_visible());
        press(&mut w, "enter", t0, 600);
        press(&mut w, "admin", t0, 610);
        assert_eq!(w.username.pending_blink(), timer(t0, 1140, 3));
        w
    }

    #[test]
    fn departed_field_timer_toggles_the_active_field_when_tags_match() {
        // Go control, 200 ms username dwell: early password caret reversed,
        // late checkpoint plain before the password's own 530 ms deadline.
        let t0 = Instant::now();
        let mut w = username_typed(t0);
        press(&mut w, "enter", t0, 810);
        press(&mut w, "abcdefghijklmn", t0, 850);
        assert_eq!(w.step, Step::EnterPassword);
        assert!(w.password.cursor_visible());
        assert_eq!(w.password.pending_blink(), timer(t0, 1380, 3));
        assert_eq!(
            w.username.pending_blink(),
            timer(t0, 1140, 3),
            "Blur leaves the username timer outstanding"
        );
        assert_eq!(w.next_blink(), Some(at(t0, 1140)));
        w.blink(at(t0, 1140));
        assert!(
            !w.password.cursor_visible(),
            "the username timer toggled the password caret"
        );
        assert!(!w.username.cursor_visible());
        assert_eq!(w.username.pending_blink(), None);
        assert_eq!(
            w.password.pending_blink(),
            timer(t0, 1670, 4),
            "accepting re-armed the password, canceling its own timer"
        );
        w.blink(at(t0, 1380));
        assert!(
            !w.password.cursor_visible(),
            "the canceled deadline delivers nothing"
        );
        w.blink(at(t0, 1670));
        assert!(w.password.cursor_visible());
        assert_eq!(w.password.pending_blink(), timer(t0, 2200, 5));
    }

    #[test]
    fn departed_field_timer_with_a_stale_tag_is_dropped() {
        // Go control, 650 ms dwell: the username ticks before leaving, and
        // both password checkpoints stay reversed.
        let t0 = Instant::now();
        let mut w = username_typed(t0);
        w.blink(at(t0, 1140));
        assert!(!w.username.cursor_visible(), "own tick while active");
        assert_eq!(w.username.pending_blink(), timer(t0, 1670, 4));
        press(&mut w, "enter", t0, 1260);
        press(&mut w, "abcdefghijklmn", t0, 1300);
        assert_eq!(w.password.pending_blink(), timer(t0, 1830, 3));
        assert_eq!(w.next_blink(), Some(at(t0, 1670)));
        w.blink(at(t0, 1670));
        assert!(w.password.cursor_visible(), "tag 4 is not the password's 3");
        assert_eq!(w.username.pending_blink(), None, "consumed by delivery");
        assert_eq!(w.password.pending_blink(), timer(t0, 1830, 3));
        w.blink(at(t0, 1830));
        assert!(!w.password.cursor_visible());
    }

    #[test]
    fn several_departed_timers_stay_bounded_and_only_the_current_tag_matches() {
        let t0 = Instant::now();
        let mut w = LoginWizard::new();
        for (name, ms) in [
            ("enter", 0),
            ("http://h:8000", 0),
            ("enter", 5),
            ("enter", 5),
            ("admin", 10),
            ("enter", 20),
            ("abcdefghijklmn", 30),
        ] {
            press(&mut w, name, t0, ms);
        }
        assert_eq!(outstanding(&w), 3, "one timer per field at most");
        assert_eq!(w.url.pending_blink(), timer(t0, 530, 3));
        assert_eq!(w.username.pending_blink(), timer(t0, 540, 3));
        assert_eq!(w.password.pending_blink(), timer(t0, 560, 3));
        // All three are due: one message per call, earliest first.
        w.blink(at(t0, 560));
        assert!(!w.password.cursor_visible(), "the URL timer matched");
        assert_eq!(w.url.pending_blink(), None);
        assert_eq!(w.password.pending_blink(), timer(t0, 1090, 4));
        w.blink(at(t0, 560));
        assert!(
            !w.password.cursor_visible(),
            "the username timer now carries a stale tag"
        );
        assert_eq!(w.username.pending_blink(), None);
        assert_eq!(outstanding(&w), 1);
        assert_eq!(w.next_blink(), Some(at(t0, 1090)));
    }

    #[test]
    fn refocusing_a_field_cancels_its_departed_timer() {
        let t0 = Instant::now();
        let mut w = username_typed(t0);
        press(&mut w, "enter", t0, 700);
        press(&mut w, "abcdefghijklmn", t0, 720);
        press(&mut w, "esc", t0, 800);
        assert_eq!(w.step, Step::EnterUsername);
        // Focus's discarded BlinkCmd (tag 4) canceled the 1140 ms username
        // timer; textinput.Blink armed tag 5.
        assert_eq!(w.username.pending_blink(), timer(t0, 1330, 5));
        assert!(w.username.cursor_visible());
        assert_eq!(
            w.password.pending_blink(),
            timer(t0, 1250, 3),
            "the departed password keeps its timer"
        );
        assert_eq!(w.next_blink(), Some(at(t0, 1250)));
        w.blink(at(t0, 1250));
        assert!(
            w.username.cursor_visible(),
            "tag 3 is stale for the username"
        );
        assert_eq!(w.password.pending_blink(), None);
        w.blink(at(t0, 1330));
        assert!(!w.username.cursor_visible());
    }

    #[test]
    fn blink_at_a_selection_step_is_dropped_and_paste_rearms_the_active_field() {
        let t0 = Instant::now();
        let mut w = LoginWizard::new();
        press(&mut w, "down", t0, 0);
        press(&mut w, "enter", t0, 0);
        press(&mut w, "enter", t0, 0);
        assert_eq!(w.step, Step::EnterProfileName);
        assert_eq!(w.profile_name.pending_blink(), timer(t0, 530, 2));
        // Esc to the cloud auth choice returns textinput.Blink with no active
        // input (dropped); the departed field keeps its timer.
        press(&mut w, "esc", t0, 100);
        assert_eq!(w.step, Step::ChooseCloudAuth);
        assert_eq!(w.profile_name.pending_blink(), timer(t0, 530, 2));
        let mut session = WizardSession {
            wizard: w,
            exists: |_: &str| false,
        };
        assert_eq!(session.next_tick(), Some(at(t0, 530)));
        session.tick(at(t0, 530));
        assert_eq!(session.next_tick(), None);
        let mut w = session.wizard;
        press(&mut w, "enter", t0, 600);
        assert_eq!(w.profile_name.pending_blink(), timer(t0, 1130, 4));
        // A pasted chunk moves the cursor: its BlinkCmd replaces the timer.
        w.paste_at("x", at(t0, 700));
        assert_eq!(w.profile_name.value(), "defaultx");
        assert_eq!(w.profile_name.pending_blink(), timer(t0, 1230, 5));
        w.blink(at(t0, 1130));
        assert!(
            w.profile_name.cursor_visible(),
            "the replaced deadline delivers nothing"
        );
    }
}
