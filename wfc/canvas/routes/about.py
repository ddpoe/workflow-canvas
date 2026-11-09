"""About routes: the version menu's answer and the bug report's new-issue URL.

``GET /api/about`` answers what the version menu shows: the installed wfc,
the update offer (or none), the installed version's changelog, the footer's
setting and the guide links. It reads ``[updates]`` through Persistence's
``read_config`` and hands the settings, with Layout's per-user file for the
check's answer, to ``wfc.release``, which starts the
once-a-day check in the background when it is due; the route never waits on
the network. ``GET /api/about/report`` asks Environments' Docker probe and
answers the prefilled new-issue URL; nothing is sent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from ... import layout, release
from ...environments import docker_server_version
from ...persistence import read_config
from ..state import _server_project_root

router = APIRouter()

#: The guide pages the menu links, under the installed version's docs channel.
_KNOWN_ISSUES_PAGE = "reference/known-issues.html"
_UPDATES_GUIDE_PAGE = "reference/wf-canvas-toml.html#updates"
_PLUGIN_INSTALL_PAGE = "tutorials/work-with-an-ai-agent.html#install"

#: The footer's sentence for each update setting: what the check offers, then
#: what to set in wf-canvas.toml to change it.
_FOOTER_TEXT = {
    "stable": "Stable releases only. For betas, set `prereleases = true` in wf-canvas.toml",
    "prereleases": ("Stable and beta releases. For stable only, set "
                    "`prereleases = false` in wf-canvas.toml"),
    "off": "Update check is off. To turn it on, set `check = true` in wf-canvas.toml",
}


class ChangelogSection(BaseModel):
    """One ``### <Heading>`` block of a version's changelog entry."""

    heading: str
    intro: str
    items: list[str]


class ChangelogVersion(BaseModel):
    """One version's changelog entry, Upgrading first and Known issues last."""

    version: str
    date: str
    sections: list[ChangelogSection]


class ReleaseNotes(BaseModel):
    """The installed version's entries, or the Changelog link when there are none."""

    versions: list[ChangelogVersion]
    link: str | None


class UpdateNotes(BaseModel):
    """The offered version's Upgrading and Known issues, and the link to all its notes."""

    sections: list[ChangelogSection]
    link: str


class UpdateOffer(BaseModel):
    """A newer release the installed wfc is offered, with its pip command."""

    version: str
    prerelease: bool
    command: str
    notes: UpdateNotes


class AboutFooter(BaseModel):
    """What the update check offers and how to change it, or why it cannot run."""

    state: Literal["stable", "prereleases", "off", "invalid"]
    text: str


class AboutLinks(BaseModel):
    """The menu's links, in the installed version's docs channel."""

    docs: str
    known_issues: str
    updates_guide: str
    plugin_install: str
    changelog: str


class AboutResponse(BaseModel):
    """What the version menu shows."""

    version: str | None
    offer: UpdateOffer | None
    changelog: ReleaseNotes
    footer: AboutFooter
    links: AboutLinks


class BugReportResponse(BaseModel):
    """The new-issue page Report a bug opens."""

    url: str


def _footer(installed: str | None, updates: dict) -> AboutFooter:
    """The footer for the effective update setting."""
    state: Literal["stable", "prereleases", "off"]
    if not updates["check"]:
        state = "off"
    elif release.includes_prereleases(installed, updates["prereleases"]):
        state = "prereleases"
    else:
        state = "stable"
    return AboutFooter(state=state, text=_FOOTER_TEXT[state])


@router.get("/api/about", response_model=AboutResponse)
def get_about(since: str | None = None):
    """Answer the version menu: version, offer, changelog, footer and links.

    Reads the project's ``[updates]``; with the check on, answers the cached
    offer at once and starts the background check when it is due. An
    invalid config still answers the version and the changelog, with no
    offer and the config's refusal as the footer text; a missing
    ``wf-canvas.toml`` answers the same, the footer naming the file.

    Args:
        since: The version the page saw last. Given, the changelog holds
            every version newer than it up to the installed one, newest
            first; omitted, the installed version alone.
    """
    installed = release.installed_wfc_version()
    root = _server_project_root()
    try:
        updates = read_config(root)["updates"]
    except FileNotFoundError:
        offer = None
        missing = layout.marker_path(root).relative_to(root).as_posix()
        footer = AboutFooter(state="invalid", text=(
            f"No {missing} in this project, so the update check cannot run. "
            "Run `wfc init` to create it"))
    except ValueError as exc:
        offer = None
        footer = AboutFooter(state="invalid", text=str(exc))
    else:
        offer = release.poll_update(installed, check=updates["check"],
                                    prereleases=updates["prereleases"],
                                    path=layout.update_check_path(Path.home()))
        footer = _footer(installed, updates)
    return AboutResponse(
        version=installed,
        offer=offer,
        changelog=release.installed_changelog(installed, since),
        footer=footer,
        links=AboutLinks(
            docs=release.docs_url(installed),
            known_issues=release.docs_url(installed, _KNOWN_ISSUES_PAGE),
            updates_guide=release.docs_url(installed, _UPDATES_GUIDE_PAGE),
            plugin_install=release.docs_url(installed, _PLUGIN_INSTALL_PAGE),
            changelog=release.changelog_url(),
        ),
    )


@router.get("/api/about/report", response_model=BugReportResponse)
def get_bug_report():
    """Answer the new-issue URL Report a bug opens, prefilled for the user to edit.

    Runs Environments' Docker probe now (never when the menu opens); the
    body names the wfc version, the platform, Python and Docker (``not
    running`` when it does not answer), and no project path.
    """
    docker = docker_server_version()
    return BugReportResponse(url=release.bug_report_url(release.installed_wfc_version(), docker))
