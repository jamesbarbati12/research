# Research Desk — Equity Coverage Site

This is a static site (no build step) — just HTML/CSS/JS. Structure:

```
index.html                       ← the landing page / coverage grid
reports/
  servicenow-analysis.html       ← your ServiceNow deep-dive
  servicenow-2026-plan.html      ← your ServiceNow FY2026 plan
```

## Getting it live on GitHub Pages (no command line needed)

1. **Create a repo.** Go to github.com → click the **+** in the top right → **New repository**.
   - If you want it at `https://<yourusername>.github.io` directly, name the repo exactly `<yourusername>.github.io`.
   - If you'd rather it live at a sub-path like `https://<yourusername>.github.io/research`, name it `research` (or anything you like) — either works fine.
   - Set it to **Public**. Don't add a README/gitignore (you already have files).

2. **Upload the files.** On the new repo's page, click **Add file → Upload files**, then drag in `index.html`, the `README.md`, and the whole `reports` folder (GitHub preserves folder structure on drag-and-drop). Commit directly to `main`.

3. **Turn on Pages.** In the repo, go to **Settings → Pages**. Under "Build and deployment," set **Source** to `Deploy from a branch`, branch `main`, folder `/ (root)`. Save.

4. **Wait ~1 minute**, then refresh that Pages settings page — it'll show your live URL at the top (either `https://<yourusername>.github.io` or `https://<yourusername>.github.io/research`, depending on what you named the repo).

That URL is what goes on your resume.

## Adding a new report later

1. Drop the new report's HTML file into `reports/` (via "Add file → Upload files" again, or `git push` if you're using the command line).
2. In `index.html`, find one of the `.card.pending` blocks (e.g. the Rocket Lab one) and:
   - Change `<div class="card ...pending">` to `<a class="card ...">` (drop `pending`, change the tag to `<a>`, matching a live card above)
   - Add `href="reports/your-file-name.html"`
   - Update the one-line description and the meta row date
3. Commit the change — Pages redeploys automatically within a minute or two.

## Notes

- The "Résumé," "LinkedIn," and "Email" links in the masthead are placeholders (`href="#"`) — swap in your real links before sharing.
- Everything is a static file — no backend, no build tools, no dependencies to install.
