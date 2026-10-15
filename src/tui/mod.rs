//! Native interactive workflows: one terminal lifecycle, Bubble Tea-shaped
//! input, shared editors and theme, and per-workflow screens with owned
//! state, pure reducers and explicit effects.
//!
//! Shared facilities (`terminal`, `input`, `editor`, `style`) are the only
//! terminal/editor/theme implementations; workflow modules keep private
//! layout helpers but never their own guards, renderers or palettes.

pub(crate) mod clipboard;
pub(crate) mod editor;
pub(crate) mod input;
mod json;
pub(crate) mod style;
pub(crate) mod terminal;

pub(crate) mod admin;
pub(crate) mod device;
pub(crate) mod login;
pub(crate) mod profile;
pub(crate) mod promql;
pub(crate) mod promql_service;
pub(crate) mod saved;
pub(crate) mod sql;
pub(crate) mod sql_service;

#[allow(unused_imports)]
pub(crate) use input::{Event, Key, KeyCode};
#[allow(unused_imports)]
pub(crate) use terminal::{Closed, Screen, SessionError, Size, Stamped, TerminalSession};
