% ===========================================================================
% OntoMem write-time theory in Answer Set Programming (clingo).
%
% This is an INDEPENDENT second implementation of the rules in core/reasoner.py.
% The Python reasoner processes events one at a time and maintains indexes;
% this program states the same rules declaratively over the whole event set.
% core/asp/crosscheck.py runs both on random event sequences and reports any
% disagreement (differential testing).
%
% Input facts
%   event(E, T, Who, What, Aspect, Intent, Ctx, Pol).   T: integer, strictly increasing
%   broader(C, D).           direct skos:broader (C is narrower than D)
%   sign(Pol, S).            likes=1, dislikes=-1, avoids=-1, neutral=0
% Output atoms
%   node/1  active/1  retired/1  succ/2  needs_conf/1  exception/2  refines/2
% ===========================================================================

% --- ontology: strict transitive closure, and the reflexive order on concepts
anc(C, D) :- broader(C, D).
anc(C, E) :- anc(C, D), broader(D, E).
concept(C) :- event(_, _, _, C, _, _, _, _).
cle(C, C) :- concept(C).
cle(C, D) :- concept(C), anc(C, D).

% --- contexts: "any" is broader than every context (a string, like all event values)
ctx(X) :- event(_, _, _, _, _, _, X, _).
ctx("any").
xle(X, X) :- ctx(X).
xle(X, "any") :- ctx(X).

% --- events that share a slot key (who, what, aspect, intent, context)
key(W, C, A, I, X) :- event(_, _, W, C, A, I, X, _).
inkey(E, W, C, A, I, X) :- event(E, _, W, C, A, I, X, _).
time(E, T) :- event(E, T, _, _, _, _, _, _).
pol(E, P) :- event(E, _, _, _, _, _, _, P).
samekey(E1, E2) :- inkey(E1, W, C, A, I, X), inkey(E2, W, C, A, I, X), E1 != E2.

% prev(E2, E1): E1 is the event immediately before E2 in the same slot
between(E1, E2) :- samekey(E1, E2), samekey(E1, E3), samekey(E2, E3),
                   time(E1, T1), time(E2, T2), time(E3, T3), T1 < T3, T3 < T2.
prev(E2, E1) :- samekey(E1, E2), time(E1, T1), time(E2, T2), T1 < T2, not between(E1, E2).

% R5 reinforce: same slot, same value as the slot's current value -> no new node.
% (The current value always equals the polarity of the latest event in the slot,
%  so comparing with the immediately preceding event is enough.)
absorbed(E2) :- prev(E2, E1), pol(E1, P), pol(E2, P).
node(E) :- event(E, _, _, _, _, _, _, _), not absorbed(E).

% R1 supersede: a node is retired when a later node exists in the same slot
retired(N) :- node(N), node(M), samekey(N, M), time(N, TN), time(M, TM), TN < TM.
active(N) :- node(N), not retired(N).
nbetween(N, M) :- node(N), node(M), node(K), samekey(N, K), samekey(M, K),
                  time(N, TN), time(M, TM), time(K, TK), TN < TK, TK < TM.
succ(N, M) :- node(N), node(M), samekey(N, M), time(N, TN), time(M, TM), TN < TM, not nbetween(N, M).

% time of the latest event in a node's slot (its last confirmation)
keylast(W, C, A, I, X, L) :- key(W, C, A, I, X), L = #max { T : event(_, T, W, C, A, I, X, _) }.
last(N, L) :- active(N), inkey(N, W, C, A, I, X), keylast(W, C, A, I, X, L).

% strictly narrower in the product order (concept, context), same frame (who, aspect, intent)
lt(N, M) :- active(N), active(M), N != M,
            inkey(N, W, C1, A, I, X1), inkey(M, W, C2, A, I, X2),
            cle(C1, C2), xle(X1, X2), (C1, X1) != (C2, X2).

opp(N, M) :- pol(N, P1), pol(M, P2), sign(P1, S1), sign(P2, S2), S1 * S2 < 0.
same(N, M) :- pol(N, P1), pol(M, P2), sign(P1, S), sign(P2, S), S != 0.

% R2 exception and refinement (derived, state-based)
exception(N, M) :- lt(N, M), opp(N, M).
refines(N, M)   :- lt(N, M), same(N, M).

% R3 broad override: a broader, opposite, MORE RECENTLY confirmed slot flags the narrower one
needs_conf(N) :- lt(N, M), opp(N, M), last(N, TN), last(M, TM), TM > TN.

#show node/1.
#show active/1.
#show retired/1.
#show succ/2.
#show needs_conf/1.
#show exception/2.
#show refines/2.
