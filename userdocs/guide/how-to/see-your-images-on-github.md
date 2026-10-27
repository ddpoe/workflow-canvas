<!-- generated from pm_mvp::docs/consumer/how-to/see-your-images-on-github @ 72c6a07cad8d; do not edit -->

# How-to: See Your Images on GitHub

## See your images on GitHub

When a project's registry is GHCR (`ghcr.io`, GitHub's container registry), each environment image you push becomes a package on GitHub. This page shows where to find your packages, how a package's pages match what wfc records, and how to fix the two things `wfc share` checks on GitHub: the package's connection to the repository, and its visibility. [Share a Project](sharing-a-project.md) covers the rest of sharing: the repository, the archive, logins and who needs which access.

To try it on a small project first, push the demo's image: [Share the demo](../tutorials/wfc-demo.md#share-the-demo).

## Find your packages

- On the repository's main page, the right sidebar lists the packages connected to the repository under **Packages**.
- On your GitHub profile, the **Packages** tab (`https://github.com/<owner>?tab=packages`) lists every package you own, connected or not.

Each environment is one package. With the registry base `ghcr.io/<owner>/my-project`, the environment `my-env` is the package `my-project/my-env`.

## Read a package page

A package page lists the package's versions. Each version is one image of the environment, tagged `fp-` and the first 12 characters of the environment's fingerprint: the identity wfc records for what the image runs. `wfc push-env` prints each image it pushes under that tag, for example `my-env:fp-fde24f9d4d5f`. Registering a changed environment and pushing it adds a version with a new tag. The older versions stay, so the method versions that use them still run.

## Check the connection to the repository

wfc labels each image it pushes with the project's GitHub repository, and GitHub connects the package to that repository. A connected package:

- is listed under **Packages** on the repository's main page;
- takes the repository's access: whoever can read the repository can pull the image, and whoever can write to it can push;
- shows the repository under **Repository source** at the top of its **Package settings**.

`wfc share`'s packages row checks each pushed package. When it reports one as not connected, for example an image pushed while the project had no GitHub remote:

1. Open the package's page. `wfc share` prints its link.
2. In the **Link this package to a repository** box at the bottom of the page, click **Connect Repository**.
3. From the list of your repositories, choose the one `wfc share` names (`<owner>/<repo>`), and confirm.
4. Run `wfc share` again.

The **Add Repository** button under **Manage Actions access** in the package settings does not connect the package. It only lets that repository's GitHub Actions workflows use the package.

## Change a package's visibility

A new package is private, and its visibility is its own: changing the repository's visibility does not change it. This is step 2 of the [visibility checklist](sharing-a-project.md#visibility-checklist):

1. On the package page, open **Package settings**.
2. Under **Danger Zone**, click **Change package visibility**.

A package made public cannot be made private again, so check what the image installs first ([Private and public images](sharing-a-project.md#private-and-public-images)).

## Next steps

- [Share a Project](sharing-a-project.md): the repository, the archive and the registry, and who needs which access.
- [Share the demo](../tutorials/wfc-demo.md#share-the-demo): the sharing steps on the demo project.
