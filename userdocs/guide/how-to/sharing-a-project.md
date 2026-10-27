<!-- generated from pm_mvp::docs/consumer/how-to/sharing-a-project @ f6554f864119; do not edit -->

# How-to: Share a Project

## Share a project

A wfc project keeps its pieces in three places, and everyone who works on it needs access to each:

- the **git repository**: your code, the project config, and the record of every registered method and environment;
- the **archive** (the DVC remote): the bytes of samples and outputs;
- the **image registry**: the container image of each environment.

This page covers putting the repository on GitHub, what each place holds, where each login lives, who needs which access, and how to make a project public. `wfc share` checks the project from your side and names the command that fixes each gap.

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

- `.wfc/wf-canvas.toml`: the project config, including the archive location (`[dvc] url`) and the registry base (`[registry] url`). It holds addresses only, never a password or token.
- `.wfc/envs.json` and `.wfc/captures/`: each registered environment, the identity of its image, the packages the image installed, and, once the image is pushed, its digest in the registry.
- `.wfc/registry/`: the registered copy of each method.
- `.dvc/config`, `.gitignore` and `.gitattributes`.
- Your own source, such as `src/`, once you commit it.

These stay on the machine that made them:

- `.wfc/wfc.db`, the database of registrations, runs and lineage.
- The DVC cache (`.dvc/cache/`) and the run outputs and restored samples under `.wfc/`. Their bytes reach other machines through the archive.
- `.dvc/config.local`, DVC's settings for this machine only, such as an archive's credentials.
- The environment images in Docker, until they are pushed to the registry.

[Project Anatomy](../explanation/project-anatomy.md) shows the full layout.

## Where credentials live

Each tool keeps its own login, and wfc stores none: it never reads, asks for or saves a token.

| Place | Where the login lives | How to log in |
|---|---|---|
| GitHub | `gh`'s login, or git's own credential helper or SSH key | `gh auth login` |
| Archive (DVC remote) | DVC's settings for this machine (`.dvc/config.local`) or the cloud provider's own login | for example `aws configure` for an `s3://` archive; a folder or network drive needs only file access |
| Registry | Docker's credential store | `docker login <host>` |

For GHCR (GitHub's registry), Docker logs in with your GitHub login. Give `gh` the packages scope, then pass its token to Docker:

```bash
gh auth login --scopes write:packages
gh auth token | docker login ghcr.io -u <your GitHub user> --password-stdin
```

If `gh` is already logged in, add the scope with `gh auth refresh --scopes write:packages`. Someone who only pulls images needs `read:packages` instead. `wfc share` prints the `docker login` line with your user filled in.

Everyone logs in on their own machine with their own account, so nobody shares a token.
`wfc share`'s package check uses the same `gh` login. If the login has no packages scope, the packages row says so and names the command that adds it: `gh auth refresh --hostname github.com --scopes write:packages` (`read:packages` if you only pull).

## Access by role

| Role | Git | Archive (DVC remote) | Registry |
|---|---|---|---|
| Collaborator who runs pipelines | write | read and write | pull |
| Collaborator who registers environments | write | read and write | push |
| Reviewer or reproducer | read | read | pull (nothing for a public image) |
| Cluster job | read | read and write | pull (a token for a private image) |

On GitHub, add collaborators in the repository's **Settings > Collaborators** (under **Access**), with **Add people**. Repository access also covers each image connected to the repository (see [Private and public images](#private-and-public-images)). Archive access is given wherever the archive lives: the bucket's permissions, or the shared folder's.

A cluster job has no browser to log in with. Give it a token it can read, for example a GitHub personal access token with `read:packages` for a private GHCR image, and log Docker in with it before the job runs.

## Push images and check what is missing

Once the project has a registry base, `wfc register-env` pushes each image it builds, and each bring-your-own `local/` image, before it records the environment. Pass `--no-push` to keep that image on this machine.

`wfc push-env` pushes the images that are still only on this machine, including the older images that earlier method versions record:

```bash
wfc push-env my-env      # one environment's images
wfc push-env --all       # every environment's images
```

It shows what it will push and asks before it does; `--yes` answers for you. The first run also sets the registry base. With a GitHub remote it proposes `ghcr.io/<owner>/<repo>`, and after you confirm it writes `[registry] url` to `.wfc/wf-canvas.toml` and commits it. For another registry, pass `--registry <url>`. The base is set once: renaming the repository later does not move where images go, and a later `--registry` naming another base is refused. To try out a registry, such as a local one, do it in a throwaway project. An image that is no longer on this machine is named in the output, and the command exits with 1.

An image is pushed under its environment's name, and a registry accepts only some names: lower-case letters and digits joined by `.`, `_`, `__` or `-`, starting and ending with a letter or digit. An environment can be registered under a name outside that rule (for example `MyTools`) and still run on this machine. When the project has a registry, `wfc register-env` refuses such a name before it builds anything. `wfc push-env` lists the image as `cannot push`, pushes the others, and exits with 1. To fix a name that cannot be pushed, do one of:

- register the image again under a valid name (`wfc register-env <new-name> ...`), set `env: <new-name>` in each method's `method.yaml` and run `wfc register-method`;
- pass `--no-push` to `wfc register-env`, which keeps the image on this machine;
- run `wfc delete-env <name>` when nothing needs the environment (it refuses while a method version records it, and names those versions).

When a collaborator runs a step whose image is not on their machine, wfc pulls it from the registry by the digest recorded at the push, checks that it is the same image, and runs it.

`wfc share` checks the project from your side and changes nothing:

```bash
wfc share
```

It prints one row per check:

- **remote**: the project has a git remote to clone.
- **login**: Docker is logged in to the registry.
- **base**: a registry base is set.
- **images**: every recorded image is in a registry.
- **packages**: on GHCR, each pushed package is connected to the repository. This row asks GitHub through `gh`; when `gh` is missing or not logged in, the row says it cannot tell and prints a link to each package's page.
- **versions**: every method version whose image is neither on this machine nor in the registry, with the commands that make it runnable again.

Each row that warns names the command that fixes it. Fix them in order and run `wfc share` again. When the images go to GHCR, the last line links [See Your Images on GitHub](see-your-images-on-github.md). `wfc doctor` shows the registry and versions rows as well.

In the canvas, the Registry tab's Envs table shows each environment's image as **pushed**, **not pushed**, **no registry**, **cannot push** (hover for the reason), **missing** or **unknown** (Docker is not running). Registering a method there says where its image is: in the registry, not pushed yet (with the `wfc push-env` command to run), or staying on this machine when the project has no registry.

## Private and public images

An environment image holds only the environment: the method code comes from the project and the data from the archive. Anyone can pull a public image and read everything in it, packages included, so keep an image private when it installs licensed or internal packages.

In a project with a GitHub remote, every image wfc pushes to GHCR is labelled with that repository, bring-your-own `local/` images and the demo image included. GHCR then connects the image's package to the repository, and the package takes the repository's access: whoever can read the repository can pull the image, and whoever can write to it can push. The package's visibility is set on its own settings page.

A package that is not connected, such as one pushed while the project had no GitHub remote, does not follow the repository's access. `wfc share` prints a link to the package's page on GitHub. Open it, click **Connect Repository** in the **Link this package to a repository** box at the bottom of the page, and choose the project's repository in the list, by its `<owner>/<repo>` name (`wfc share` names it). [See Your Images on GitHub](see-your-images-on-github.md) shows each package page and what it means.

Pulling a private image needs a login with `read:packages` on every machine that pulls it, a cluster included. Pulling a public image needs no login.

A bring-your-own image that already lives in another registry is not copied to yours. It is pulled from its own registry, with whatever access that registry gives.

## Visibility checklist

Making a project public takes three separate switches, each on its own page. wfc never makes anything public. Go through them in order, and leave private any one you want to keep private.

1. **The repository.** On GitHub, the repository's **Settings > General > Danger Zone > Change repository visibility**. You can switch it back to private later.
2. **Each environment image.** On GitHub, each package's **Package settings > Danger Zone > Change package visibility**. Every package has its own switch, and changing the repository's visibility does not change it. GitHub's documentation says a package made public cannot be made private again, so check the image's package list before you switch it. The repository's main page lists its packages under **Packages**.
3. **The archive.** A public repository with a private archive keeps its samples and outputs private. Allowing anonymous reads on the archive (a public bucket, or a folder anyone can read) publishes the data, so check your data-sharing agreements and ethics approvals first.

After the switches, run `wfc share` to confirm that every image is pushed and connected to the repository.

## Next steps

- [Registering an Environment](../tutorials/registering-an-environment.md): build an environment's image with `wfc register-env`.
- [Project Anatomy](../explanation/project-anatomy.md): what a project holds and what git tracks.
- [Storage and Provenance](../explanation/storage-and-provenance.md): the archive and how other machines read results from it.
- [CLI Reference](../reference/cli-reference.md): every `wfc` command and flag.
- [See Your Images on GitHub](see-your-images-on-github.md): your packages, each package page and its settings, matched to what wfc records.
