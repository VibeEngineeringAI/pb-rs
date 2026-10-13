//! encoding/json semantics for the user and role inventory responses.
//!
//! Each decoder walks the ordered document like json.Unmarshal: field names
//! fold case, later duplicates overwrite earlier values, null leaves strings
//! and structs unchanged but resets maps, slices and pointers, typed slices
//! reuse retained element storage, and the first type mismatch in document
//! order is the returned error. Go map keys have no order; these decoders
//! keep first-insertion order as Rust's stable presentation.
use crate::gojson::{self, Node};
use serde::Serialize;
use serde_json::Value;
use std::collections::{HashMap, HashSet};

/// json.Unmarshal's error text; callers choose the public classification.
pub(super) type Decoded<T> = Result<T, String>;

/// encoding/json foldName for the ASCII field names used here.
fn folded(key: &str) -> String {
    key.chars()
        .map(|c| match c {
            '\u{17f}' => 's',
            '\u{212a}' => 'k',
            _ => c.to_ascii_lowercase(),
        })
        .collect()
}

/// UnmarshalTypeError.Error.
fn mismatch(value: &Node, target: &str, structure: &str, path: &str) -> String {
    if structure.is_empty() && path.is_empty() {
        format!(
            "json: cannot unmarshal {} into Go value of type {target}",
            value.kind()
        )
    } else {
        format!(
            "json: cannot unmarshal {} into Go struct field {structure}.{path} of type {target}",
            value.kind()
        )
    }
}

/// A string field: null keeps the previous value.
fn string(slot: &mut String, value: &Node, structure: &str, path: &str) -> Decoded<()> {
    match value {
        Node::Scalar(Value::Null) => Ok(()),
        Node::Scalar(Value::String(text)) => {
            slot.clone_from(text);
            Ok(())
        }
        other => Err(mismatch(other, "string", structure, path)),
    }
}

/// map[string]T keys with Go replacement semantics and stable insertion order.
#[derive(Default)]
struct Keys {
    ordered: Vec<String>,
    seen: HashSet<String>,
}
impl Keys {
    fn insert(&mut self, key: &str) {
        if !self.seen.contains(key) {
            self.seen.insert(key.to_owned());
            self.ordered.push(key.to_owned());
        }
    }
    fn clear(&mut self) {
        self.ordered.clear();
        self.seen.clear();
    }
}

/// GET user: `[]cmd.UserData`; only the identifiers are retained.
pub(super) fn user_ids(body: &[u8]) -> Decoded<Vec<String>> {
    let document = gojson::decode_ordered(body)?;
    let items = match &*document {
        Node::Scalar(Value::Null) => return Ok(Vec::new()),
        Node::Array(items) => items,
        other => return Err(mismatch(other, "[]cmd.UserData", "", "")),
    };
    let mut ids = Vec::with_capacity(items.len());
    for item in items {
        let mut id = String::new();
        let mut method = String::new();
        match item {
            Node::Scalar(Value::Null) => (),
            Node::Object(fields) => {
                for (key, value) in fields {
                    match folded(key).as_str() {
                        "id" => string(&mut id, value, "UserData", "id")?,
                        "method" => string(&mut method, value, "UserData", "method")?,
                        _ => (),
                    }
                }
            }
            other => return Err(mismatch(other, "cmd.UserData", "", "")),
        }
        ids.push(id);
    }
    Ok(ids)
}

/// GET roles: `map[string]json.RawMessage`; values are arbitrary JSON.
pub(super) fn role_names(body: &[u8]) -> Decoded<Vec<String>> {
    let document = gojson::decode_ordered(body)?;
    let mut keys = Keys::default();
    match &*document {
        Node::Scalar(Value::Null) => (),
        Node::Object(fields) => {
            for (key, _) in fields {
                keys.insert(key);
            }
        }
        other => {
            return Err(mismatch(other, "map[string]json.RawMessage", "", ""));
        }
    }
    Ok(keys.ordered)
}

#[derive(Clone, Debug, Default, PartialEq, Serialize)]
pub(crate) struct Resource {
    #[serde(skip_serializing_if = "String::is_empty")]
    pub(crate) dataset: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub(crate) tag: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub(crate) stream: String,
}
impl Resource {
    /// cmd.roleDataset: the preferred field never erases the legacy one.
    pub(crate) fn dataset(&self) -> &str {
        if self.dataset.is_empty() {
            &self.stream
        } else {
            &self.dataset
        }
    }
}

/// cmd.RoleData (and the identically shaped cmd.UserRoleAction).
#[derive(Clone, Debug, Default, PartialEq, Serialize)]
pub(crate) struct Action {
    pub(crate) privilege: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) resource: Option<Resource>,
}

/// Go type names that appear in an action list's decode errors.
struct ActionTypes<'a> {
    /// The struct owning the `actions` field ("" for an anonymous struct).
    owner: &'a str,
    /// The error field path of `actions`.
    path: &'a str,
    slice: &'a str,
    element: &'a str,
    /// The element struct's name.
    action: &'a str,
}

/// A `[]Action` field. Arrays decode into retained element storage, even
/// beyond a shorter duplicate's length; null and `[]` discard the storage.
#[derive(Default)]
struct Actions {
    storage: Vec<Action>,
    len: usize,
    present: bool,
}
impl Actions {
    fn decode(&mut self, value: &Node, types: &ActionTypes<'_>) -> Decoded<()> {
        match value {
            Node::Scalar(Value::Null) => {
                self.storage.clear();
                self.len = 0;
                self.present = false;
            }
            Node::Array(items) => {
                if items.is_empty() {
                    self.storage.clear();
                }
                for (index, item) in items.iter().enumerate() {
                    if index == self.storage.len() {
                        self.storage.push(Action::default());
                    }
                    action(&mut self.storage[index], item, types)?;
                }
                self.len = items.len();
                self.present = true;
            }
            other => return Err(mismatch(other, types.slice, types.owner, types.path)),
        }
        Ok(())
    }
    fn finish(mut self) -> Option<Vec<Action>> {
        self.present.then(|| {
            self.storage.truncate(self.len);
            self.storage
        })
    }
}

fn action(result: &mut Action, value: &Node, types: &ActionTypes<'_>) -> Decoded<()> {
    let fields = match value {
        Node::Scalar(Value::Null) => return Ok(()),
        Node::Object(fields) => fields,
        other => return Err(mismatch(other, types.element, types.owner, types.path)),
    };
    for (key, value) in fields {
        match folded(key).as_str() {
            "privilege" => string(
                &mut result.privilege,
                value,
                types.action,
                &format!("{}.privilege", types.path),
            )?,
            "resource" => match value {
                Node::Scalar(Value::Null) => result.resource = None,
                Node::Object(fields) => {
                    // A non-nil pointer is decoded into, merging duplicates.
                    let resource = result.resource.get_or_insert_with(Resource::default);
                    for (key, value) in fields {
                        let (slot, name) = match folded(key).as_str() {
                            "dataset" => (&mut resource.dataset, "dataset"),
                            "tag" => (&mut resource.tag, "tag"),
                            "stream" => (&mut resource.stream, "stream"),
                            _ => continue,
                        };
                        string(
                            slot,
                            value,
                            "RoleResource",
                            &format!("{}.resource.{name}", types.path),
                        )?;
                    }
                }
                other => {
                    return Err(mismatch(
                        other,
                        "cmd.RoleResource",
                        types.action,
                        &format!("{}.resource", types.path),
                    ));
                }
            },
            _ => (),
        }
    }
    Ok(())
}

const ROLE_WRAPPER: &str = "struct { Actions []cmd.RoleData \"json:\\\"actions\\\"\" }";
const ROLE_ACTIONS: ActionTypes<'static> = ActionTypes {
    owner: "",
    path: "actions",
    slice: "[]cmd.RoleData",
    element: "cmd.RoleData",
    action: "RoleData",
};

/// GET role/NAME: `struct { Actions []cmd.RoleData }`. Missing or null
/// actions stay nil (JSON null); an empty array is a non-nil empty slice.
pub(super) fn role_actions(body: &[u8]) -> Decoded<Option<Vec<Action>>> {
    let document = gojson::decode_ordered(body)?;
    let mut actions = Actions::default();
    match &*document {
        Node::Scalar(Value::Null) => (),
        Node::Object(fields) => {
            for (key, value) in fields {
                if folded(key) == "actions" {
                    actions.decode(value, &ROLE_ACTIONS)?;
                }
            }
        }
        other => return Err(mismatch(other, ROLE_WRAPPER, "", "")),
    }
    Ok(actions.finish())
}

/// Validate one cmd.UserServerRole map value. Map elements start zeroed.
fn server_role(value: &Node, field: &str, types: &ActionTypes<'_>) -> Decoded<()> {
    match value {
        Node::Scalar(Value::Null) => Ok(()),
        Node::Object(fields) => {
            let mut actions = Actions::default();
            for (key, value) in fields {
                if folded(key) == "actions" {
                    actions.decode(value, types)?;
                }
            }
            Ok(())
        }
        other => Err(mismatch(
            other,
            "cmd.UserServerRole",
            "UserRolesResponse",
            field,
        )),
    }
}

const DIRECT_ACTIONS: ActionTypes<'static> = ActionTypes {
    owner: "UserServerRole",
    path: "roles.actions",
    slice: "[]cmd.UserRoleAction",
    element: "cmd.UserRoleAction",
    action: "UserRoleAction",
};
const GROUP_ACTIONS: ActionTypes<'static> = ActionTypes {
    owner: "UserServerRole",
    path: "group_roles.actions",
    slice: "[]cmd.UserRoleAction",
    element: "cmd.UserRoleAction",
    action: "UserRoleAction",
};

/// GET user/ID/role: cmd.UserRolesResponse flattened like `pb user list`:
/// direct role names, then each group's role names. A role assigned both
/// directly and through groups appears once per source.
pub(super) fn user_role_names(body: &[u8]) -> Decoded<Vec<String>> {
    let document = gojson::decode_ordered(body)?;
    let mut direct = Keys::default();
    // Group names in first-insertion order, each with its role-name map.
    let mut groups: HashMap<String, usize> = HashMap::new();
    let mut members: Vec<Keys> = Vec::new();
    match &*document {
        Node::Scalar(Value::Null) => (),
        Node::Object(fields) => {
            for (key, value) in fields {
                match folded(key).as_str() {
                    "roles" => match value {
                        Node::Scalar(Value::Null) => direct.clear(),
                        Node::Object(entries) => {
                            for (name, role) in entries {
                                server_role(role, "roles", &DIRECT_ACTIONS)?;
                                direct.insert(name);
                            }
                        }
                        other => {
                            return Err(mismatch(
                                other,
                                "map[string]cmd.UserServerRole",
                                "UserRolesResponse",
                                "roles",
                            ));
                        }
                    },
                    "group_roles" => match value {
                        Node::Scalar(Value::Null) => {
                            groups.clear();
                            members.clear();
                        }
                        Node::Object(entries) => {
                            for (group, roles) in entries {
                                // Each map element decodes into a fresh inner map.
                                let mut inner = Keys::default();
                                match roles {
                                    Node::Scalar(Value::Null) => (),
                                    Node::Object(roles) => {
                                        for (name, role) in roles {
                                            server_role(role, "group_roles", &GROUP_ACTIONS)?;
                                            inner.insert(name);
                                        }
                                    }
                                    other => {
                                        return Err(mismatch(
                                            other,
                                            "map[string]cmd.UserServerRole",
                                            "UserRolesResponse",
                                            "group_roles",
                                        ));
                                    }
                                }
                                match groups.get(group.as_str()) {
                                    Some(&index) => members[index] = inner,
                                    None => {
                                        groups.insert(group.clone(), members.len());
                                        members.push(inner);
                                    }
                                }
                            }
                        }
                        other => {
                            return Err(mismatch(
                                other,
                                "map[string]map[string]cmd.UserServerRole",
                                "UserRolesResponse",
                                "group_roles",
                            ));
                        }
                    },
                    _ => (),
                }
            }
        }
        other => {
            return Err(mismatch(other, "cmd.UserRolesResponse", "", ""));
        }
    }
    let mut names = direct.ordered;
    for inner in members {
        names.extend(inner.ordered);
    }
    Ok(names)
}
