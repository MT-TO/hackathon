const checkboxes = Array.from(document.querySelectorAll("[data-selection-checkbox]"));
const selectedCount = document.querySelector("[data-selected-count]");
const selectAllButton = document.querySelector("[data-select-all]");
const clearSelectionButton = document.querySelector("[data-clear-selection]");
const themeToggleButton = document.querySelector("[data-theme-toggle]");
const themeLabel = document.querySelector("[data-theme-label]");
const favoriteButtons = Array.from(document.querySelectorAll("[data-favorite-toggle]"));
const deleteImageButtons = Array.from(document.querySelectorAll("[data-delete-image]"));
const removableTags = Array.from(document.querySelectorAll("[data-removable-tag]"));
const tagContextMenu = document.createElement("div");
const tagContextMenuAction = document.createElement("button");
let activeTagTarget = null;

tagContextMenu.className = "context-menu";
tagContextMenu.hidden = true;
tagContextMenuAction.type = "button";
tagContextMenuAction.className = "context-menu-action danger";
tagContextMenu.appendChild(tagContextMenuAction);
document.body.appendChild(tagContextMenu);

function submitHiddenForm(action, fields) {
  const form = document.createElement("form");
  form.method = "post";
  form.action = action;
  form.hidden = true;

  fields.forEach(([name, value]) => {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    form.appendChild(input);
  });

  document.body.appendChild(form);
  form.submit();
}

function updateSelectionCount() {
  if (!selectedCount) {
    return;
  }
  const total = checkboxes.filter((checkbox) => checkbox.checked).length;
  selectedCount.textContent = String(total);
}

function applyTheme(theme) {
  const resolvedTheme = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = resolvedTheme;
  localStorage.setItem("photo-desk-theme", resolvedTheme);

  if (!themeLabel) {
    return;
  }
  themeLabel.textContent = resolvedTheme === "dark" ? "Mode clair" : "Mode sombre";
}

function closeTagContextMenu() {
  tagContextMenu.hidden = true;
  activeTagTarget = null;
}

function openTagContextMenu(target, x, y) {
  activeTagTarget = target;
  const tagValue = target.dataset.tag || "ce tag";
  tagContextMenuAction.textContent = `Supprimer le tag "${tagValue}"`;
  tagContextMenu.hidden = false;

  const { innerWidth, innerHeight } = window;
  const menuWidth = 220;
  const menuHeight = 48;
  const left = Math.min(x, innerWidth - menuWidth - 12);
  const top = Math.min(y, innerHeight - menuHeight - 12);

  tagContextMenu.style.left = `${Math.max(12, left)}px`;
  tagContextMenu.style.top = `${Math.max(12, top)}px`;
}

if (checkboxes.length) {
  checkboxes.forEach((checkbox) => {
    checkbox.addEventListener("change", updateSelectionCount);
  });
  updateSelectionCount();
}

if (selectAllButton) {
  selectAllButton.addEventListener("click", () => {
    checkboxes.forEach((checkbox) => {
      checkbox.checked = true;
    });
    updateSelectionCount();
  });
}

if (clearSelectionButton) {
  clearSelectionButton.addEventListener("click", () => {
    checkboxes.forEach((checkbox) => {
      checkbox.checked = false;
    });
    updateSelectionCount();
  });
}

if (themeToggleButton) {
  const initialTheme = document.documentElement.dataset.theme || "dark";
  applyTheme(initialTheme);

  themeToggleButton.addEventListener("click", () => {
    const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(nextTheme);
  });
}

favoriteButtons.forEach((button) => {
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    closeTagContextMenu();

    const relativePath = button.dataset.relativePath;
    const mode = button.dataset.mode;
    const next = button.dataset.next || "/";
    if (!relativePath || !mode) {
      return;
    }

    submitHiddenForm("/actions/favorite", [
      ["relative_path", relativePath],
      ["mode", mode],
      ["next", next],
    ]);
  });
});

deleteImageButtons.forEach((button) => {
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    closeTagContextMenu();

    const relativePath = button.dataset.relativePath;
    const filename = button.dataset.filename || "cette image";
    const next = button.dataset.next || "/";
    if (!relativePath) {
      return;
    }

    if (!window.confirm(`Supprimer définitivement "${filename}" ?`)) {
      return;
    }

    submitHiddenForm("/actions/delete-image", [
      ["relative_path", relativePath],
      ["next", next],
    ]);
  });
});

removableTags.forEach((tag) => {
  tag.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    event.stopPropagation();
    openTagContextMenu(tag, event.clientX, event.clientY);
  });
});

tagContextMenuAction.addEventListener("click", (event) => {
  event.preventDefault();
  event.stopPropagation();

  const target = activeTagTarget;
  closeTagContextMenu();
  if (!target) {
    return;
  }

  const relativePath = target.dataset.relativePath;
  const tagValue = target.dataset.tag;
  const next = target.dataset.next || "/";
  if (!relativePath || !tagValue) {
    return;
  }

  submitHiddenForm("/actions/remove-tag", [
    ["relative_path", relativePath],
    ["tag", tagValue],
    ["next", next],
  ]);
});

document.addEventListener("click", () => {
  closeTagContextMenu();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeTagContextMenu();
  }
});
