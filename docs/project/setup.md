# Creating the watch-game Claude Project

Projects are created only in the Claude app (web, desktop or mobile); nothing
in this repo can create one for you. Each step says which file goes in which
field. Copy each file whole. These steps mirror the rover's
(github.com/shogowarrior/rover, docs/project/setup.md).

| File | Goes in |
|---|---|
| [goal.md](goal.md) | the Project's **Goal** field. The project conversation reads it. |
| [instructions.md](instructions.md) | the Project's **Project instructions** field. It is sent to every thread, so it repeats the standing goals. |
| [environment.sh](environment.sh) | the cloud environment's **setup script** |
| [handoff.md](handoff.md) | nowhere: it stays in the repo, and threads read it |

## Steps

1. **GitHub access.** The Project reaches GitHub through the account connected
   to Claude. On 2026-10-03, BeeBeRBaB could read `shogowarrior/watch-game` but
   not push to it. If BeeBeRBaB is the connected account, give it write access
   (repo Settings > Collaborators), as was done for the rover.
2. **Create the Project** on the repository `shogowarrior/watch-game`, branch
   `main`.
3. **Goal:** paste [goal.md](goal.md).
4. **Project instructions:** paste [instructions.md](instructions.md).
5. **Environment.** The setup script belongs to a cloud environment, not to
   the Project. Environments are shared by every Project that picks them, so
   make one for watch-game:
   - At claude.ai/code, click the cloud icon above the message box, open
     **Cloud**, and click **Add cloud environment**.
   - **Name:** `watch-game`.
   - **Setup script:** paste [environment.sh](environment.sh). It installs
     Python 3 and Node 24, and caches the one npm package the MicroPython test
     runner needs.
   - **Network access:** **Custom**, allowing the distribution's apt mirrors,
     `deb.nodesource.com`, `registry.npmjs.org` and GitHub. **Full** also
     works.
6. **Point the Project at it:** in the Project, open **Project settings >
   Environment** and pick `watch-game`.
7. **Models and effort:** use the gear icon in the Project's header > **Project
   settings** > **General**. Set **Thread model** and **Thread effort** for the
   threads, and **Coordinator model** and **Coordinator effort** for the
   project conversation.
8. **Start it.** Send:

   > Read docs/project/handoff.md, ask me its open questions, then start at
   > step 1 of its next steps.

## Keeping these in step

- When you edit a field in the Project, update its file here in the same
  change.
- [handoff.md](handoff.md) carries the current work. A thread updates it
  before merging, so the next thread finds it current.
- A rule that belongs to the repo goes in AGENTS.md or CLAUDE.md, which every
  thread loads anyway. The instructions carry only what a cloud thread cannot
  get from the repo: the owner's standing goals and preferences, the cloud's
  limits, and the setup.
