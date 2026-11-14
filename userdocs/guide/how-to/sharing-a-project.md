<!-- generated from pm_mvp::docs/consumer/how-to/sharing-a-project @ f5c847f6dc6c; do not edit -->

# Share a Project

## Share a project

A Workflow Canvas (wfc) project keeps its pieces in three places, and everyone who works on it needs access to each:

- the **git repository**: your code, the project config, and the record of every registered method and environment;
- the **archive**: where wfc keeps a backup copy of your samples and results;
- the **image registry**: the container image of each environment.

`wfc share` checks the project from your side and names the command that fixes each gap.

To learn the steps before you do them on your own project, try them on the demo first: [Share the demo](../tutorials/wfc-demo.md#share-the-demo).


## Put the repository on GitHub

wfc commits its own files as you work, but it never pushes them. To put the project on GitHub, install the GitHub CLI (`gh`), log in once, and create a private repository from the project root:

```bash
gh auth login
gh repo create my-project --private --source . --push
```

`--source .` makes the current directory the repository, and `--push` pushes its commits. When the project has no remote, `wfc share` prints this command with your folder's name filled in.

From then on, push as you would any repository: the commits wfc makes when you register methods and environments stay on your machine until you run `git push`.

On another git host, create an empty repository there, then connect and push:

```bash
git remote add origin <url>
git push -u origin HEAD
```


## What git holds

A collaborator who clones the repository gets:

- `.wfc/wf-canvas.toml`: the project config, including the archive location (`[dvc] url`) and the registry base (`[registry] url`).
- `.wfc/envs.json` and `.wfc/captures/`: each registered environment and the packages its image installed.
- `.wfc/registry/`: the registered copy of each method.
- `.dvc/config`, `.gitignore` and `.gitattributes`.
- Your own source, such as `src/`, once you commit it.

These stay on the machine that made them:

- `.wfc/wfc.db`, the database of registrations, runs and lineage.
- The cached copies of your samples and run outputs. They reach other machines through the archive.
- `.dvc/config.local`, DVC's settings for this machine only, such as an archive's credentials.
- The environment images in Docker, until they are pushed to the registry.

[Project Anatomy](../explanation/project-anatomy.md) shows the full layout.


## What sharing through git does not carry

Cloning the repository gives a collaborator your code, your registered methods and your environment records. It does not give them your runs, samples or outputs: those stay in the `.wfc/wfc.db` on your machine. A collaborator registers the samples again from the data with `wfc register-sample`. To hand over the data, write a bundle with `wfc export-run`: it always holds the samples the runs read, with their outputs when you add `--with-outputs`, and the images when you add `--with-image` (needed for an image that is not pushed to a registry); see [Share Runs as a Bundle Anyone Can Rerun](share-bundle.md).

## Where credentials live

Each tool keeps its own login; wfc stores none.

| Place | Where the login lives | How to log in |
|---|---|---|
| GitHub | `gh`'s login, or git's own credential helper or SSH key | `gh auth login` |
| Archive | DVC's settings for this machine (`.dvc/config.local`) or the cloud provider's own login | for example `aws configure` for an `s3://` archive; a folder or network drive needs only file access |
| Registry | Docker's credential store | `docker login <host>` |

For GHCR (GitHub's registry), Docker logs in with your GitHub login. Give `gh` the packages scope, then pass its token to Docker:

```bash
gh auth login --scopes write:packages
gh auth token | docker login ghcr.io -u <your GitHub user> --password-stdin
```

If `gh` is already logged in, add the scope with `gh auth refresh --scopes write:packages`. Someone who only pulls images needs `read:packages` instead. `wfc share` prints the `docker login` line with your user filled in.

Everyone logs in on their own machine with their own account, so nobody shares a token.


## Access by role

| Role | Git | Archive | Registry |
|---|---|---|---|
| Collaborator who runs pipelines | write | read and write | pull |
| Collaborator who registers environments | write | read and write | push |
| Reviewer or reproducer | read | read | pull (nothing for a public image) |
| Cluster job | read | read and write | pull (a token for a private image) |

On GitHub, add collaborators in the repository's **Settings > Collaborators** (under **Access**), with **Add people**. Repository access also covers each image connected to the repository (see [Private and public images](#private-and-public-images)). Archive access is given wherever the archive lives: the bucket's permissions, or the shared folder's.

A cluster job has no browser to log in with. Give it a token it can read, for example a GitHub personal access token with `read:packages` for a private GHCR image, and log Docker in with it before the job runs.


## Push images and check what is missing

Once the project has a registry, `wfc register-env` pushes each image it builds or uses. Pass `--no-push` to keep that image on this machine.

`wfc push-env` pushes the images that are still only on this machine, including the older images that earlier method versions record:

```bash
wfc push-env my-env      # one environment's images
wfc push-env --all       # every environment's images
```

It shows what it will push and asks before it does; `--yes` answers for you. The first run also sets where images go: with a GitHub remote it proposes `ghcr.io/<owner>/<repo>` and saves it to the project config after you confirm. For another registry, pass `--registry <url>`.

To move the project's images to another registry, such as a lab organisation or a registry your cluster can reach, name it with `--registry`:

```bash
wfc push-env my-env --registry ghcr.io/my-lab/my-project   # switch, and push my-env there
wfc push-env --all                                          # push every other image there
```

The plan shows the switch and asks before it makes it. Until an image is pushed to the new registry, wfc pulls it from the old one, so run `wfc push-env --all` to move every image. wfc does not delete the old images; remove them from the old registry once every collaborator has the new commit.

An image is pushed under its environment's name, and a registry accepts only some names: lower-case letters and digits joined by `.`, `_`, `__` or `-`, starting and ending with a letter or digit. `wfc push-env` lists the image as `cannot push`, pushes the others, and exits with 1. To fix a name that cannot be pushed, do one of:

- register the image again under a valid name (`wfc register-env <new-name> ...`), set `env: <new-name>` in each method's `method.yaml` and run `wfc register-method`;
- pass `--no-push` to `wfc register-env`, which keeps the image on this machine;

When a collaborator runs a step whose image is not on their machine, wfc pulls it from the registry.

`wfc share` checks what a collaborator needs to run your methods, from your side, and changes nothing:

```bash
wfc share
```

It prints one row per check:

- **remote**: the project has a git remote to clone.
- **login**: Docker is logged in to the registry.
- **base**: a registry base is set.
- **images**: every recorded image is in a registry.
- **packages**: on GHCR, each pushed package is connected to the repository.
- **versions**: every method version whose image is neither on this machine nor in the registry, with the commands that make it runnable again.

Use `wfc doctor` to check that the archive is reachable.

Each row that warns names the command that fixes it. Fix them in order and run `wfc share` again.

In the canvas, the Registry tab's Envs table shows whether each environment's image is pushed; hover a status for the reason. Registering a method there says where its image is: in the registry, not pushed yet (with the `wfc push-env` command to run), or staying on this machine when the project has no registry.


## Move the archive to another place

The archive location is the `url` under `[dvc]` in `.wfc/wf-canvas.toml`. `wfc init --archive` sets it only for a project that has none; it is refused once one is set. To move an existing project's archive:

1. Copy the contents of the old archive to the new place, with the tool that fits it (a file copy for a folder, your cloud tool for a bucket).
2. Edit `url` under `[dvc]` in `.wfc/wf-canvas.toml` to the new location.
3. Run `wfc init` in the project. It points the project at the new archive.
4. Commit `.wfc/wf-canvas.toml` and `.dvc/config`, so collaborators get the new location when they pull.

Then run `wfc doctor` to check that the new archive is configured and reachable, and `wfc cache push` to push anything the new archive does not hold yet.


## Private and public images

An environment image holds only the environment: the method code comes from the project and the data from the archive. Anyone can pull a public image and read everything in it, packages included, so keep an image private when it installs licensed or internal packages.

In a project with a GitHub remote, every image wfc pushes to GHCR is linked to that repository. GHCR then connects the image's package to the repository, and the package takes the repository's access: whoever can read the repository can pull the image, and whoever can write to it can push. The package's visibility is set on its own settings page.

A package that is not connected, such as one pushed while the project had no GitHub remote, does not follow the repository's access. [Check the connection to the repository](see-your-images-on-github.md#check-the-connection-to-the-repository) shows how to connect it.

With no git remote, for example when you hand over a rerun bundle, there is no repository to connect a package to. Give each recipient access on the package's settings page, in its **Manage access** section; `wfc share` links each page. The recipient logs Docker in with a GitHub login that holds `read:packages`:

```bash
gh auth login --scopes read:packages
gh auth token | docker login ghcr.io -u <their GitHub user> --password-stdin
```

[See Your Images on GitHub](see-your-images-on-github.md) shows each package page and what it means.


## Visibility checklist

Making a project public takes three separate switches, each on its own page. Go through them in order, and leave private any one you want to keep private.

1. **The repository.** On GitHub, the repository's **Settings > General > Danger Zone > Change repository visibility**. You can switch it back to private later.
2. **Each environment image.** On GitHub, each package's **Package settings > Danger Zone > Change package visibility**. Every package has its own switch, and changing the repository's visibility does not change it. GitHub's documentation says a package made public cannot be made private again, so check the image's package list before you switch it. The repository's main page lists its packages under **Packages**.
3. **The archive.** A public repository with a private archive keeps its samples and outputs private. Allowing anonymous reads on the archive (a public bucket, or a folder anyone can read) publishes the data, so check your data-sharing agreements and ethics approvals first.

After the switches, run `wfc share` to confirm that every image is pushed and connected to the repository.


## Next steps

- [Register an Environment](../tutorials/registering-an-environment.md): build an environment's image with `wfc register-env`.
- [Project Anatomy](../explanation/project-anatomy.md): what a project holds and what git tracks.
- [Storage & Provenance](../explanation/storage-and-provenance.md): the archive and how other machines read results from it.
- [Command Line](../reference/command-line.md): every `wfc` command and flag.
- [See Your Images on GitHub](see-your-images-on-github.md): your packages, each package page and its settings, matched to what wfc records.

<!-- group_index: nested pages -->
```{toctree}
:hidden:

/how-to/see-your-images-on-github
/how-to/share-bundle
```
