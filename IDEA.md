# How to stop an AI from marking its own homework

Two ideas. Neither is about AI, really. Both are stolen from places that
solved this problem a long time ago.

## 1. Delete the box instead of guarding it

Most systems have a checkbox that says **done**. Then they write rules around it:
don't tick it unless you really finished. Reviewers check. Instructions get
sterner. Someone adds a linter.

The checkbox is the problem. As long as it exists, whoever can tick it can lie —
by accident, by optimism, or because ticking it ends an unpleasant task.

So get rid of it. Have no `done` field anywhere. Every time something asks
"is this finished?", work the answer out *fresh* from whatever proof is lying
around: is there a receipt? did the test actually run? did it pass?

Now lying isn't forbidden, it's **impossible**. There is nothing to lie in.
You can't fake "done" because "done" isn't a value you write, it's a result
that gets computed.

This is how a bank balance works. You don't type what you think your balance is.
It's added up from real transactions. You can't just declare yourself rich.

## 2. Two different minds, not two different moods

Say you want your work checked. You could check it again yourself, more carefully
this time. That barely helps — you'll miss the same things twice, because your
blind spots travel with you.

Same for a model. Asking one to double-check itself, or asking it again while
saying "be strict now", mostly re-runs the same blind spots. It's the same mind
in a different mood.

A *different* model — trained differently, by different people, on different
material — is wrong about *different things*. Where one is confidently mistaken,
the other often just isn't. The mistakes don't line up.

So: one does the work, a genuinely different one checks it. Not because the
second is smarter. Because it's wrong in other places.

This is proofreading. Reading your own writing a fourth time finds almost
nothing. Handing it to someone else finds the typo instantly — not because they
read better, but because they haven't already decided what the sentence says.

## Why they belong together

Idea 1 stops you claiming work you didn't do.
Idea 2 stops you doing it badly in ways you can't see.

You need both. Computed status with one model catches lies but not blind spots.
Two models with a writable `done` field catches blind spots, then lets either one
tick the box anyway.

## What this does not do

Worth stating plainly, because a guarantee that gets overstated is worse than
none — someone will rely on it.

**It stops the accident, not the arsonist.** Forgetting to check your work
becomes impossible. Deliberately writing fake receipts is still possible. What
changes is that faking becomes a *distinct, deliberate act* that leaves evidence
behind, rather than something that happens quietly by omission. That is a real
improvement and it is all it is.

**It cannot read the receipt.** A machine can check that a citation was recorded.
It cannot check that the cited page says what you claim, or that the URL exists.
That gap needs a reader — which is idea 2's job, and is why idea 2 isn't
optional garnish.

**It only covers what you wrote down.** A question nobody ever asked is invisible
to every check. Silence is the cheapest way to look finished.

**"Different model" has to be true.** Two instances of the same model with
different names and different instructions are not independent, however
different their job titles. If the system can't record *which* model actually
did each piece, "independently verified" is decoration.

## Someone else got there too

In July 2026 a paper landed with the same core thesis, arrived at independently:
**"Proof-or-Stop: Don't Trust the Agent, Trust the Evidence"**
([arXiv:2607.14890](https://arxiv.org/abs/2607.14890)). Its abstract says the
method *"treats agent outputs as claims rather than lifecycle state"* and permits
transitions *"only when fresh, tracked-source-state-bound, mechanically
verifiable evidence satisfies the relevant gate."* It cites the same four
standards this project does.

Two people finding the same shape independently is a good sign, not a
disappointing one. It means the problem is real rather than a private
preoccupation.

What still appears unbuilt anywhere is the combination of computed status,
evidence bound to a claim by digest, **and** an audit whose model identity is
recorded — so the run can prove it was checked by a different model, and be
blocked if it can't. Naming a different judge is a solved, common feature.
Proving one was used is not.

## The standard is why nobody did this

Here is the part worth knowing if you work with assurance cases.

The relevant international standard, OMG SACM v2.3, defines a claim's status
like this:

> `assertionDeclaration:AssertionDeclaration[1] = asserted` — *the declaration
> indicating the state of the Assertion.*

A stored field, with a default of "asserted", changed by setting a value. Every
tool that conforms to the standard therefore inherits **a writable `done` box** —
the exact thing idea 1 removes. The specification hands you the problem.

Which is why this project borrows the standard's *vocabulary* — the words for
goals, strategies and evidence — and deliberately does not implement its data
model. Conforming fully would have meant taking the box back.

Not a criticism of the standard. It was written for human engineers documenting
their reasoning, where an editable status field is exactly right. It just doesn't
survive contact with an author that can edit its own record.

## Where this came from

The `empirica` plugin in this repo is one implementation, for one tool
(Claude Code). The plugin is the proof it works. The two ideas above are the part
worth taking.

Both were found the hard way. The plugin's own audit step failed its first real
run and was right to: the evidence proved a conditional — *if* the check fires,
the bug appears — while never establishing that the check fires at all. One of
its tests turned out to be indistinguishable from having no test. Neither would
have been caught by the thing that wrote them.

Which is the argument, really.
