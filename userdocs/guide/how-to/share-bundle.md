<!-- generated from pm_mvp::docs/consumer/how-to/share-bundle @ 95d25605d4d4; do not edit -->

# Share Runs as a Bundle Anyone Can Rerun

## Share runs as a bundle anyone can rerun

`wfc export-run` packages runs, and every run above them, as one zip. The person who receives it does not need Workflow Canvas (wfc): they unzip it and run `python rerun.py`, which reruns each step in Docker and compares every output with the recorded one.

**The zip always holds the samples those runs read**, so whoever receives it gets that data. Check your data-sharing agreements before you send it. This is the difference from sharing the project, which carries no samples ([What sharing through git does not carry](sharing-a-project.md#what-sharing-through-git-does-not-carry)). The options below add the outputs and the images; the samples are in every bundle.

```bash
wfc export-run --run 412 --out bundle.zip
```

- `--run <run-id>`: a run to include, with every run it read from. Repeat the flag for more runs.
- `--out <file.zip>`: the zip to write. It must be a file path, not a directory.
- `--with-outputs`: also put the original output files in the zip, so the receiver can compare them with the rerun's. Without it the zip holds only their hashes.
- `--with-image`: also save each step's container image in the zip, for a receiver who cannot pull it.
- `--yes`: answer yes when a step's image was never pushed to a registry and `--with-image` is not given.

The command prints the plan first. If anything stops the bundle from being complete, for example a run that did not finish, it lists every reason and writes nothing.

The Canvas does the same: in the History tab, **Export run** in a run's detail panel, or **Export runs** after selecting several, opens a dialog with **Include outputs** and **Include images** options. Changing either shows the plan again before anything downloads.

### Rerun a bundle

The receiver unzips the file, then runs this in the unzipped folder:

```bash
python rerun.py
```

It needs Python 3.11 or newer and Docker. It prints `same` or `DIFFERENT` for each output. The rerun's files go in a new `rerun-<time>/` folder, or in `--work DIR`. The bundle's `README.md` describes everything in it.

### What the receiver needs

- **With images included** (`--with-image`, or **Include images** in the Canvas): Python 3.11 or newer and Docker. The zip holds the samples, the code and the images, so nothing else is needed.
- **Without images:** the same, and each image must be pullable from the project's registry. A public image needs no login. A private GHCR image needs a GitHub login with `read:packages` and read access to the repository; see [Access by role](sharing-a-project.md#access-by-role).

:::{warning}
An image that was never pushed to a registry has to be included, or the receiver cannot run that step.
:::

### If a step says DIFFERENT

`same` means the rerun wrote exactly the file that was recorded. `DIFFERENT` means the bytes differ, which on its own does not mean the result changed. Common causes that leave the result unchanged:

- the file records when it was made (PDFs and many image formats store a creation date);
- a different CPU or number of threads changes the last digits of a number;
- rows written in whatever order a parallel job finished;
- randomness without a fixed seed.

To find out:

1. Start at the first difference `rerun.py` names. Later differences often follow from it.
2. Compare the rerun's file with the original in `outputs/<step>/`: open both figures, or compare tables by value with a tolerance. A bundle exported without `--with-outputs` has no originals to compare, so **export with `--with-outputs` if the receiver should be able to compare**.
3. Small numeric differences that stay small further down the pipeline are expected between machines. A difference that changes a conclusion needs a full investigation from that step: a gene crossing a significance threshold, or a sample landing in a different cluster. Check that `rerun.py` reported the image as matching, compare the input hashes, and read the step's logs in `rerun-<time>/logs/`; the original run's logs are in `steps/<step>/logs/`.

To make your own outputs rerun the same, see [Writing outputs that rerun the same](../tutorials/authoring-a-method-script.md#writing-outputs-that-rerun-the-same).
