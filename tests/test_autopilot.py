#!/usr/bin/env python3
"""autopilot asks its questions once, confirms a run card, then runs the rest of the loop alone until one stated
terminal state; it never decides an action on the pause list and merges only through the repository's normal path."""
import json
import re
import unittest

import support

ROOT = support.ROOT
SKILL = ROOT / 'skills/autopilot/SKILL.md'
CARD = ROOT / 'skills/autopilot/run-card.md'
# The terminal states and the pause list agreed in .scratch/autopilot/alignment.md (WHAT 4) and ledger U1.
STATES = ('`delivered`', '`delivered-local`', '`merged`', '`waiting-on-user`', '`paused-allowance`', '`paused-by-user`',
          '`blocked`')
PAUSES = ('login or permission prompt', 'credentials', 'spending money', 'deleting data', 'production change',
          'merging without `--auto-merge`', 'missing approval')


def prose(path):
    return ' '.join(path.read_text().split())


class RunCard(unittest.TestCase):
    def test_the_card_holds_every_field_the_stop_condition_needs(self):
        text = CARD.read_text()
        for field in ('## Goal', '## Acceptance criteria', '## Stop condition', '## Granted actions',
                      '## Pause list', '## Assumptions', '## State'):
            self.assertIn(field, text)
        state = text.split('## State', 1)[1]
        for key in ('confirmed:', 'session:', 'auto-merge:', 'phase:', 'terminal:', 'resume:'):
            self.assertIn(key, state)
        self.assertIn('.scratch/<slug>/autopilot.md', prose(SKILL))

    def test_the_card_is_workflow_state_that_a_rule_about_project_files_does_not_forbid(self):
        # Seen live on Codex: "no new files" in the goal kept the card out of .scratch/ and it lived only in chat.
        self.assertIn('a principle about the project\'s files never keeps them out of `.scratch/`', prose(SKILL))

    def test_the_goal_form_runs_aligns_interview_before_the_card(self):
        start = ' '.join(SKILL.read_text().split('## Start', 1)[1].split('\n## ', 1)[0].split())
        self.assertIn('**`autopilot <goal>`:** run [align](../align/SKILL.md) on the goal, with its interview', start)

    def test_the_start_settles_verify_lines_that_tell_success_from_failure_before_the_card(self):
        # Rule: align's interview settles acceptance criteria whose Verify lines tell success from failure.
        start = prose(SKILL).split('## Start', 1)[1].split('## Run', 1)[0]
        self.assertIn('acceptance criteria with Verify lines that tell success from failure', start)

    def test_an_existing_card_continues_from_its_state_on_a_take_over(self):
        # Rule: a take-over of an existing card continues from the State it recorded.
        start = prose(SKILL).split('## Start', 1)[1].split('## Run', 1)[0]
        self.assertIn('With an existing card, continue from its State.', start)

    def test_the_user_confirms_the_card_once_and_is_asked_nothing_after(self):
        text = prose(SKILL)
        self.assertIn('asks the user to confirm it once', text)
        self.assertIn('No phase after align starts before that confirmation', text)
        self.assertIn('After confirmation, ask the user nothing', text)
        self.assertIn('Record the confirmation on the card', text)
        # Seen live on Codex: an unconfirmed card was marked `waiting-on-user`, which hides it from the bootstrap trigger.
        self.assertIn('Waiting for this confirmation is not a terminal state: `terminal:` stays empty', text)

    def test_the_card_is_re_read_after_compaction(self):
        bootstrap = prose(ROOT / 'bootstrap.md')
        self.assertIn('An autopilot run card with no terminal state', bootstrap)
        self.assertIn('that this conversation created or took over', bootstrap)
        self.assertIn('unconfirmed card starts no phase', bootstrap)
        self.assertIn('](skills/autopilot/SKILL.md)', bootstrap)


class ComposedPhases(unittest.TestCase):
    def test_the_confirmed_card_stands_for_the_checks_the_phases_ask_of_the_user(self):
        text = prose(SKILL)
        self.assertIn("stands for the user's check of newly cut and split tickets", text)
        self.assertIn('Candidate ADRs the user has not approved are not written', text)
        self.assertIn('approved ones are written as dispatch says', text)

    def test_each_phase_runs_with_the_grants_the_loop_needs(self):
        run = SKILL.read_text().split('## Run', 1)[1].split('\n## ', 1)[0]
        self.assertRegex(run, r'1\. \[refine\]\(\.\./refine/SKILL\.md\) the brief into tickets')
        self.assertRegex(run, r'\[dispatch\]\(\.\./dispatch/SKILL\.md\)[^\n]*`--autofix`')
        self.assertRegex(' '.join(run.split()), r'\[pr\]\(\.\./pr/SKILL\.md\).*push the workstream branch and open the PR')

    def test_without_a_pr_host_the_run_ends_at_local_delivery_instead_of_publishing(self):
        run = ' '.join(SKILL.read_text().split('## Run', 1)[1].split('\n## ', 1)[0].split())
        self.assertIn('In a repository with no PR host, publish nothing', run)
        self.assertIn('end `delivered-local`', run)

    def test_the_card_lists_every_pause(self):
        pauses = ' '.join(CARD.read_text().split('## Pause list', 1)[1].split('\n## ', 1)[0].split()).lower()
        for pause in ('login or permission prompts', 'credentials', 'spending money', 'deleting data',
                      'production changes', 'merging without `--auto-merge`', 'a missing approval',
                      'weakening a verify', 'narrowing a user decision'):
            self.assertIn(pause, pauses)

    def test_a_workstream_run_directly_on_main_ends_at_local_delivery(self):
        local = next(line for line in SKILL.read_text().split('## Stop', 1)[1].split('\n- ') if line.startswith('`delivered-local`'))
        self.assertIn('ran directly on `main`', ' '.join(local.split()))

    def test_choices_reserved_to_the_user_by_the_phases_are_never_decided(self):
        text = prose(SKILL)
        self.assertIn('The pause list is never decided', text)
        self.assertIn("weakening an acceptance criterion's Verify", text)
        self.assertIn('narrowing a decision the user made', text)


class StopCondition(unittest.TestCase):
    def test_each_run_ends_in_exactly_one_named_state_with_evidence(self):
        stop = SKILL.read_text().split('## Stop', 1)[1]
        listed = re.findall(r'^- (`[a-z-]+`):', stop, re.M)
        self.assertEqual(sorted(listed), sorted(STATES))
        self.assertIn('exactly one terminal state, with its evidence', prose(SKILL))

    def test_delivered_needs_passing_evidence_for_every_acceptance_criterion(self):
        self.assertIn('Never record `delivered` while any acceptance criterion lacks passing evidence', prose(SKILL))

    def test_a_pause_list_action_ends_the_run_waiting_on_the_user_instead_of_being_decided(self):
        text = prose(SKILL)
        for pause in PAUSES:
            self.assertIn(pause, text)
        self.assertIn('ends `waiting-on-user` instead of deciding it', text)

    def test_low_allowance_pauses_after_the_step_in_progress(self):
        text = prose(SKILL)
        self.assertIn('scripts/allowance.py" status', text)
        self.assertIn('`critical`', text)
        self.assertIn('Pause only when the resolver refuses a needed job for want of an eligible candidate', text)
        self.assertIn('ask the user whether to include the critical provider', text)
        self.assertIn('finish the step in progress, commit passing work and end `paused-allowance`', text)


class Flags(unittest.TestCase):
    def test_auto_merge_merges_only_through_the_normal_path(self):
        text = prose(SKILL)
        merged = next(line for line in SKILL.read_text().splitlines() if line.startswith('- `merged`'))
        self.assertIn('only with `--auto-merge`', merged)
        self.assertIn("merges only through the repository's normal path", text)
        self.assertIn('never by admin override or self-approval', text)

    def test_take_over_reuses_the_session_and_asks_only_what_is_unsettled(self):
        text = prose(SKILL)
        self.assertIn('`autopilot --take-over`', text)
        self.assertIn('reuses accepted decisions, tickets and reports', text)
        self.assertIn('asks only the align questions still unsettled', text)
        self.assertIn('A confirmed card whose goal, criteria and grants are unchanged is not confirmed again', text)
        self.assertIn('a card that ended `waiting-on-user` or `paused-allowance` resumes once its cause is gone', text)
        self.assertIn('it moves the terminal state to `history:`, clears `terminal:`', text)
        self.assertIn('history:', CARD.read_text().split('## State', 1)[1])

    def test_late_questions_are_decided_reversibly_and_reported_first(self):
        text = prose(SKILL)
        self.assertIn('ends `blocked`', text)
        self.assertIn('split tickets also fail twice', text)
        self.assertIn('prefer the reversible direction', text)
        self.assertIn('record its basis and reversibility in the ledger', text)
        self.assertIn('The final report leads with the open assumptions', text)


class AllowanceResume(unittest.TestCase):
    def test_an_allowance_pause_schedules_its_own_resume_and_asks_only_when_the_host_needs_approval(self):
        run = ' '.join(SKILL.read_text().split('## Run', 1)[1].split('\n## ', 1)[0].split())
        self.assertIn('Ending `paused-allowance`, schedule the resume instead of leaving it to the user', run)
        self.assertIn("set one timer through the host's native scheduler, named in its session part's Resume timer section, for just after the reset of the window that drove the level", run)
        self.assertIn('the latest `resets_at` of the windows at 80% used or projected, or five minutes from now when none is', run)
        self.assertIn('that runs `autopilot --take-over --timed`', run)
        self.assertIn("record the time and the timer's handle on the card", run)
        # The confirmed card is what authorizes the timer, so the card's grants name it (ADR 0012).
        self.assertIn('The confirmed run card is the authorization', run)
        self.assertIn('resume itself on `paused-allowance` with one timer', CARD.read_text().split('## Granted actions', 1)[1].split('\n## ', 1)[0])
        self.assertIn("A host whose timer needs the user's approval keeps that prompt pending", run)
        self.assertIn('a host with no scheduler gets the resume command in the report', run)

    def test_a_critical_provider_that_still_leaves_a_candidate_does_not_pause_the_run(self):
        # Rule: routing already omits a critical provider unless a layer includes it, so a run with an eligible
        # candidate for every needed job does not pause.
        run = prose(SKILL).split('## Run', 1)[1].split('## Decide without asking', 1)[0]
        self.assertIn('Routing already leaves out the candidates of a `critical` provider', run)
        self.assertIn('still leaves every needed job an eligible candidate does not pause the run', run)

    def test_a_timer_that_fires_while_still_limited_sets_the_next_only_for_a_later_reset(self):
        run = ' '.join(SKILL.read_text().split('## Run', 1)[1].split('\n## ', 1)[0].split())
        self.assertIn('If the allowance is still `critical` when the timer fires', run)
        self.assertIn('set the next timer only when the new reset is later than the one just waited for', run)
        self.assertIn('A take-over or a pause, by the timer or by the user, cancels a timer left over', run)

    def test_the_card_records_the_timer(self):
        self.assertIn('timer:', CARD.read_text().split('## State', 1)[1])


def timer_section(host, part='session'):
    text = (ROOT / f'hosts/{host}/{part}.md').read_text()
    return ' '.join(text.split('## Resume timer', 1)[1].split('\n## ', 1)[0].split())


class TimedResume(unittest.TestCase):
    def test_a_timer_resumes_only_the_card_that_is_still_waiting_on_it(self):
        start = ' '.join(SKILL.read_text().split('## Start', 1)[1].split('\n## ', 1)[0].split())
        self.assertIn('**`autopilot --take-over --timed`:** what a resume timer runs, as the user\'s own take-over, authorized on the card', start)
        self.assertIn('It resumes only a card that still ended `paused-allowance` with `timer:` set', start)
        self.assertIn("for any other state it reports the card's state and changes nothing", start)


class HostTimers(unittest.TestCase):
    def test_the_shared_text_names_no_runtime_tool_and_points_at_the_host_adapter(self):
        text = prose(SKILL)
        self.assertIn("through the host's native scheduler, named in its session part's Resume timer section", text)
        for tool in ('CronCreate', 'zloop_wake', 'launchd', 'crontab'):
            self.assertNotIn(tool, text)

    def test_claude_schedules_a_one_shot_cron_job_that_lasts_as_long_as_the_session(self):
        section = timer_section('claude')
        self.assertIn('`CronCreate` with `recurring: false`', section)
        self.assertIn("pinned to the reset's minute, hour, day and month", section)
        self.assertIn('`CronDelete`', section)

    def test_claude_prompts_in_plain_text_because_a_scheduled_prompt_cannot_run_a_user_only_skill(self):
        section = timer_section('claude')
        self.assertIn('A scheduled prompt cannot run a skill marked `disable-model-invocation`', section)
        self.assertIn("plain text that names the card and the plugin\'s autopilot skill file, by absolute path, and asks for its `--take-over --timed` procedure", section)

    def test_claude_describes_when_the_timer_survives_and_when_it_does_not(self):
        section = timer_section('claude')
        self.assertIn('Claude restores it when the same session is resumed with `claude --resume` or `claude --continue` before its time', section)
        self.assertIn('stops firing when the session exits', section)
        self.assertIn('a time that passed while it was closed is not caught up', section)
        self.assertIn('fires only while the session is idle', section)

    def test_pi_schedules_through_the_bootstrap_extensions_wake_tool(self):
        section = timer_section('pi')
        self.assertIn('`zloop_wake`', section)
        self.assertIn('`command` is `/skill:autopilot --take-over --timed`', section)
        self.assertIn('lost if Pi exits or replaces the extension (`/reload`, a new session)', section)
        self.assertIn('`cancel`', section)
        self.assertIn('Pi drops a wake that arrives while it compacts', section)
        self.assertIn('the card keeps the resume command', section)

    def test_codex_and_opencode_have_no_one_shot_timer_so_the_report_carries_the_resume_command(self):
        codex = timer_section('codex', 'workers')
        self.assertIn('Resume timer section of [workers.md](workers.md)', ' '.join((ROOT / 'hosts/codex/session.md').read_text().split()))
        self.assertIn("Codex's scheduled tasks are recurring and belong to the app, not the CLI", codex)
        self.assertIn('set no timer', codex)
        self.assertIn('`$zloop:autopilot --take-over`', codex)
        opencode = timer_section('opencode')
        self.assertIn('OpenCode has no built-in scheduler', opencode)
        self.assertIn('set no timer', opencode)
        for section in (codex, opencode):
            self.assertIn('and the time after which it can run', section)
            self.assertIn('install no operating-system timer unless the user asks', section)


class JumpIn(unittest.TestCase):
    def test_pause_ends_the_run_in_paused_by_user_with_the_users_words_and_the_stopped_phase(self):
        text = prose(SKILL)
        self.assertIn('**`autopilot --pause`:**', text)
        self.assertIn("Record `paused-by-user` with the user's words and the phase it stopped in", text)
        stop = SKILL.read_text().split('## Stop', 1)[1]
        self.assertRegex(stop, r'(?m)^- `paused-by-user`: the user took control with `--pause`')

    def test_while_paused_nothing_starts_lands_or_goes_out_and_the_user_is_asked(self):
        paused = ' '.join(SKILL.read_text().split('## While paused', 1)[1].split('\n## ', 1)[0].split())
        self.assertIn('start no worker', paused)
        self.assertIn('Workers already running finish, and their results wait unaccepted in the workstream\'s reports', paused)
        self.assertIn('Integrate, push and publish nothing', paused)
        self.assertIn('The card grants nothing until `--take-over`', paused)
        self.assertIn('ask the user what the next decision needs', paused)

    def test_taking_over_a_paused_card_keeps_what_the_user_did_and_decided_in_control(self):
        start = ' '.join(SKILL.read_text().split('## Start', 1)[1].split('\n## ', 1)[0].split())
        self.assertIn('A card that ended `paused-by-user` resumes at once, with no cause to wait for', start)
        self.assertIn('first record each decision the user made while in control in the ledger as theirs, with their words', start)
        self.assertIn('and re-read the branch and the board, so the work the user did is the current state', start)

    def test_an_ordinary_message_mid_run_is_an_instruction_not_a_pause(self):
        run = ' '.join(SKILL.read_text().split('## Run', 1)[1].split('\n## ', 1)[0].split())
        self.assertIn('A message from the user during the run is an instruction, not a pause', run)
        self.assertIn("follow it, record it in the ledger as the user's decision with their words, and ask nothing", run)
        self.assertIn("One that changes the goal, a criterion or a grant also updates the card with the user's words", run)


class EffortLevels(unittest.TestCase):
    def test_the_card_records_both_levels_on_their_own_lines_at_the_defaults(self):
        # Rule: a run card always states the dispatch and review levels, defaults included, so the user confirms
        # them with the card. A card without them has no reader.
        state = CARD.read_text().split('## State', 1)[1]
        self.assertRegex(state, r'(?m)^dispatch-effort: medium$')
        self.assertRegex(state, r'(?m)^review-effort: high$')
        self.assertIn('both effort levels on their own lines', prose(CARD))

    def test_the_start_and_a_take_over_take_the_three_flags(self):
        # Rule: `--effort` sets both steps, `--dispatch-effort` and `--review-effort` override it; no level means the
        # command's defaults, never a level remembered, and max runs only when asked.
        text = SKILL.read_text()
        hint = re.search(r'^argument-hint: (.+)$', text.split('---', 2)[1], re.M)
        assert hint, 'autopilot declares its argument hint'
        hint = hint.group(1)
        self.assertIn('<goal> [--effort low|medium|high|xhigh|max]', hint)
        self.assertIn('--take-over [--auto-merge] [--effort low|medium|high|xhigh|max]', hint)
        for flag in ('--dispatch-effort', '--review-effort'):
            self.assertEqual(hint.count(flag), 2, hint)
        start = prose(SKILL).split('## Start', 1)[1].split('## Run', 1)[0]
        for phrase in ('records both effort levels', "keeps the card's levels unless a level flag is given"):
            self.assertIn(phrase, start)
        card = prose(CARD)
        for phrase in ('`--effort` sets both steps, and a split flag overrides it for its step',
                       'A start with no level writes these defaults', 'never a level typed earlier',
                       "a take-over keeps the card's levels unless a flag is given", '`max` runs only when the user asks',
                       'an unknown flag or level value is refused',
                       'a take-over of a card without the two lines or with a line that is not a level'):
            self.assertIn(phrase, card)

    def test_the_run_passes_both_levels_to_dispatch_beside_autofix(self):
        # Rule: dispatch receives the card's levels, and its handoff runs the review at the review level.
        run = prose(SKILL).split('## Run', 1)[1].split('## Decide without asking', 1)[0]
        self.assertIn('`--autofix`', run)
        self.assertIn('`--dispatch-effort <level>` and `--review-effort <level>`', run)

    def test_a_take_over_keeps_the_levels_unless_flags_are_given(self):
        # Rule: a take-over or a timed take-over keeps the card's levels; only level flags change them.
        start = prose(SKILL).split('## Start', 1)[1].split('## Run', 1)[0]
        self.assertIn('take-over <card> [--auto-merge] [--effort <level>] [--dispatch-effort <level>] '
                      '[--review-effort <level>]', start)
        self.assertIn("keeps the card's levels unless a level flag is given", start)
        timed = SKILL.read_text().split('- **`autopilot --take-over --timed`:**', 1)[1].split('\n- **`--auto-merge`', 1)[0]
        self.assertIn("keeps the card's levels unchanged", ' '.join(timed.split()))


class Registration(unittest.TestCase):
    def test_only_the_user_starts_it_on_every_host(self):
        self.assertIn('disable-model-invocation: true', SKILL.read_text())
        policy = (ROOT / 'skills/autopilot/agents/openai.yaml').read_text()
        self.assertRegex(policy, r'(?m)^policy:\n  allow_implicit_invocation: false$')

    def test_decisions_follow_the_engineering_standard(self):
        first_read = re.search(r'^First read:.*?(?:\n\n|\Z)', SKILL.read_text(), re.M | re.S)
        assert first_read, 'autopilot has no First read paragraph'
        self.assertIn('](../../standards/engineering.md)', first_read.group(0))

    def test_pause_is_listed_where_users_see_the_command(self):
        hint = re.search(r'^argument-hint: (.+)$', SKILL.read_text().split('---', 2)[1], re.M)
        assert hint, 'autopilot declares its argument hint'
        hint = hint.group(1)
        self.assertIn('--take-over [--auto-merge] [--effort low|medium|high|xhigh|max]', hint)
        self.assertIn('| --pause', hint)
        self.assertIn('--pause', (ROOT / 'skills/autopilot/agents/openai.yaml').read_text())
        readme = next(l for l in (ROOT / 'README.md').read_text().splitlines() if l.startswith('| `autopilot` |'))
        self.assertIn('`--pause` to take control', readme)
        self.assertIn('`--effort` for both steps', readme)
        self.assertIn('`--dispatch-effort` and `--review-effort` per step', readme)
        self.assertIn('`paused-by-user`', CARD.read_text().split('## Stop condition', 1)[1].split('\n## ', 1)[0])

    def test_the_command_is_listed_where_users_and_the_doc_checker_look(self):
        self.assertRegex((ROOT / 'README.md').read_text(), r'(?m)^\| `autopilot` \|')
        self.assertIn('[`/zloop:autopilot`](../autopilot/SKILL.md)', (ROOT / 'skills/brief/pipeline.md').read_text())
        architecture = json.loads((ROOT / '.agents/skills/writing-agent-docs/architecture.json').read_text())
        self.assertEqual(architecture['modules']['autopilot']['docs'], ['skills/autopilot/**/*.md'])
        level = next(i for i, names in enumerate(architecture['levels']) if 'autopilot' in names)
        for phase in ('align', 'refine', 'dispatch', 'review', 'pr'):
            below = next(i for i, names in enumerate(architecture['levels']) if phase in names)
            self.assertLess(level, below, f'autopilot must sit above {phase} to link it')


class TakeOverCommand(unittest.TestCase):
    def test_the_approval_path_says_an_agent_never_meets_a_humans_merge_gate_and_autopilot_runs_the_take_over_command(self):
        # Rule: a merge gate the repository declares for a human is never met by the agent; a take-over is scripted.
        approval = prose(ROOT / 'runtime/board/approval-path.md')
        self.assertIn('A merge gate the repository declares for a human is never met by the agent', approval)
        start = prose(SKILL).split('## Start', 1)[1].split('## Run', 1)[0]
        self.assertIn('python3 "<plugin_root>/scripts/autopilot_card.py" take-over <card> [--auto-merge]', start)
        self.assertIn('the grant of the run that ended lapses with its terminal state', start)
        self.assertIn('`phase:` stays as recorded: a run that ends keeps the phase it stopped in', start)
        phase = next(line for line in CARD.read_text().splitlines() if line.startswith('phase:'))
        self.assertNotIn('ended', phase)


if __name__ == '__main__':
    unittest.main()
