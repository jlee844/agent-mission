"""Who leads the session -- the honest path must stay unmarked.

The end-to-end tests prove the attacks ARE marked. This proves the other
half: a person's real terminal is NOT. The strings are the session leaders
measured on the maintainer's machine on 2026-10-26, user name replaced.
"""
import pytest

from agent_mission.whoami import leader_is_a_persons_shell as person


@pytest.mark.parametrize("leader", [
    "login -pf <user>",                                       # Terminal
    "/bin/zsh -l",                                            # login shell
    "login -pfl <user> /bin/bash -c exec -la zsh /bin/zsh",   # IDE terminal
    "-zsh",                                                   # login spelling
    "zsh",                                                    # plain interactive
    "",                                                       # unreadable
])
def test_a_persons_terminal_is_not_marked(leader):
    assert person(leader) is True, leader


@pytest.mark.parametrize("leader", [
    "sh -c python -m agent_mission set objective x --on demo; true",
    "/bin/sh -c writer; true",
    "/usr/bin/python3 bypass.py",
])
def test_the_attack_shapes_are_marked(leader):
    assert person(leader) is False, leader


def test_a_dash_c_after_login_is_not_the_leaders_own():
    """The IDE terminal carries `-c`, but it belongs to bash further along;
    the LEADER is login. Reading arguments before the executable would
    have marked every IDE terminal."""
    assert person("login -pfl u /bin/bash -c exec -la zsh /bin/zsh")
