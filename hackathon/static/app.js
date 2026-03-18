const checkboxes = Array.from(document.querySelectorAll("[data-selection-checkbox]"));
const selectedCount = document.querySelector("[data-selected-count]");
const selectAllButton = document.querySelector("[data-select-all]");
const clearSelectionButton = document.querySelector("[data-clear-selection]");
const themeToggleButton = document.querySelector("[data-theme-toggle]");
const themeLabel = document.querySelector("[data-theme-label]");

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
