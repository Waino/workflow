This is a re-spec for the plan resolution.

## Terminology

To clear some confusion, we need a term for non-intent-non-review-plan: let's call it "ticket".
The new terminology:
- Plan: A plan in any state, including intent and review.
- Ticket: A plan in the states {planning, todo, implementing, checking, done, rejected}. Excludes intents and reviews.
          Tickets transition between states as work progresses.
- Intent: Intents are always in the state `intent`, they do not transition.
- Review: Reviews are always in the state `review`, they do not transition.

At the time of writing, only intent/001_idea has been updated to reflect the new terminology.
Neither the other plans nor the codebase has been updated.
TODO: Check each usage of "plan", and change to "ticket" when appropriate.

## Assumptions

1. Tickets only ever move forward through the workflow states, never backward.
2. There is only a single ticket with any given sequence number (tickets exclude intents).
   It is an error if two different tickets with the same number are found in a single repo.
3. Stale copies of a ticket can be found in other repos, if the transition hasn't been merged to that repo yet.
4. Prompt commands can do transitions that don't require a handoff between agents.
5. Transitions that do require a handoff are placed in script commands, because these can perform cross-repo git operations.
6. Script commands with a transition always involve a push to the bare repo.
7. Resolving the correct agent for a plan is important for agent routing, and checking the correct repo.
   Resolving the exact state within that agent/repo is secondary: we need it to construct the path, but not much else.
8. Script commands may have strict state requirements,
   but prompt commands only need the agent and the ticket visible to that agent (to instruct the agent what to read).

## UX goals

9. The user may want to do ad-hoc modifications to the workflow, e.g. use a prompt command in an untypical state.
   If it is possible to do so, we shouldn't prevent it just to enforce the workflow.
   For example, maybe there's an old subdivided ticket in state `planning`, that hasn't been critiqued yet.
   Before running `critique`, it is advisable to run `ask`.
   (However, this `ask` is not normal workflow for a plan that was just created in the same session).
10. When the user explicitly gives the state, (e.g. "planning/002"), that state should be used.
    If a plan in that state with that number doesn't exist, raise PlanNotFoundError.
    But if a plan in that state with that number does exist, never raise PlanNotFoundError.

## Observations and consequences

- Due to 2, the current state of a ticket can be easily found by globbing on the given hints across all repos,
  and taking the most progressed state (due to 1 and 3).
- Shouldn't need an `also_valid_in_states` field: because of 9, `ask` is automatically valid in both planner states.
