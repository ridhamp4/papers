const section = document.querySelector(".citations");

if (section) {
  const tabs = [...section.querySelectorAll(".citation-tab")];
  const previews = [...section.querySelectorAll(".citation-text")];
  const status = section.querySelector(".citation-status");

  for (const tab of tabs) {
    tab.addEventListener("click", () => {
      for (const item of tabs) {
        const selected = item === tab;
        item.classList.toggle("active", selected);
        item.setAttribute("aria-pressed", String(selected));
      }
      for (const preview of previews) {
        preview.hidden = preview.dataset.format !== tab.dataset.format;
      }
      status.textContent = "";
    });
  }

  async function copy(text, success) {
    try {
      await navigator.clipboard.writeText(text);
      status.textContent = success;
    } catch {
      status.textContent = "Copy unavailable. Select the text above to copy it.";
    }
  }

  section.querySelector(".copy-citation").addEventListener("click", () => {
    const selected = previews.find((preview) => !preview.hidden);
    if (selected) copy(selected.textContent, `${selected.dataset.format} citation copied.`);
  });
  section.querySelector(".copy-link").addEventListener("click", () => {
    copy(window.location.href, "Page link copied.");
  });
}
