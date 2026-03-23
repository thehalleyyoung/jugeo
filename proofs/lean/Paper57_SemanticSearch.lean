/-
  Paper57_SemanticSearch.lean — Sheaf-Indexed Code Search: Using the
  Semantic Site for Retrieval

  Formalises Paper 57 of the Judgment Geometry series:
    • SearchCoord     — coordinate in the code index
    • PropTag         — proposition tag for queries
    • IndexEntry      — an entry in the semantic index
    • SemanticIndex   — the full search index (list of entries)
    • Query           — a search query (set of required prop tags)
    • matchScore      — fraction of query tags satisfied by an entry
    • searchResults    — entries satisfying all query constraints
    • retrieval_sound — every returned result satisfies all query tags
    • retrieval_complete — every satisfying entry is returned
    • score_bounds    — match scores lie in valid range
    • empty_query     — an empty query returns all entries

  All theorems proved without sorry.
-/

namespace JudgmentGeometry.Paper57

-- ════════════════════════════════════════════════════════════════════
-- § 1  Coordinates and Proposition Tags
-- ════════════════════════════════════════════════════════════════════

/-- A coordinate in the code index. -/
structure SearchCoord where
  fileId : Nat
  nodeId : Nat
  deriving DecidableEq, Repr

/-- A proposition tag representing a semantic property. -/
structure PropTag where
  id : Nat
  deriving DecidableEq, Repr

-- ════════════════════════════════════════════════════════════════════
-- § 2  Index Entries and the Semantic Index
-- ════════════════════════════════════════════════════════════════════

/-- Trust level for indexed entries. -/
inductive TrustLevel where
  | low | medium | high
  deriving DecidableEq, Repr

def TrustLevel.toNat : TrustLevel → Nat
  | .low => 0 | .medium => 1 | .high => 2

/-- An entry in the semantic index: a coordinate with its verified
    proposition tags and trust level. -/
structure IndexEntry where
  coord    : SearchCoord
  tags     : List PropTag
  trust    : TrustLevel
  deriving Repr

/-- The semantic index is a list of index entries. -/
abbrev SemanticIndex := List IndexEntry

-- ════════════════════════════════════════════════════════════════════
-- § 3  Queries
-- ════════════════════════════════════════════════════════════════════

/-- A search query: a list of required proposition tags and a minimum
    trust level. -/
structure Query where
  requiredTags : List PropTag
  minTrust     : TrustLevel
  deriving Repr

/-- Check whether an entry contains a specific tag. -/
def IndexEntry.hasTag (e : IndexEntry) (t : PropTag) : Bool :=
  e.tags.any (fun t' => t' == t)

/-- Check whether an entry satisfies all required tags. -/
def satisfiesAllTags (e : IndexEntry) (tags : List PropTag) : Bool :=
  tags.all (fun t => e.hasTag t)

/-- Check whether an entry meets the trust threshold. -/
def meetsTrust (e : IndexEntry) (minTrust : TrustLevel) : Bool :=
  e.trust.toNat ≥ minTrust.toNat

/-- Full query match: all tags satisfied and trust met. -/
def matchesQuery (e : IndexEntry) (q : Query) : Bool :=
  satisfiesAllTags e q.requiredTags && meetsTrust e q.minTrust

-- ════════════════════════════════════════════════════════════════════
-- § 4  Search Results
-- ════════════════════════════════════════════════════════════════════

/-- Retrieve all entries matching a query. -/
def searchResults (index : SemanticIndex) (q : Query) : List IndexEntry :=
  index.filter (fun e => matchesQuery e q)

-- ════════════════════════════════════════════════════════════════════
-- § 5  Retrieval Soundness
-- ════════════════════════════════════════════════════════════════════

/-- Every result from `searchResults` satisfies the query.
    (Theorem 5.1: Retrieval Soundness.) -/
theorem retrieval_sound (index : SemanticIndex) (q : Query)
    (e : IndexEntry) (h : e ∈ searchResults index q) :
    matchesQuery e q = true := by
  simp [searchResults] at h
  exact h.2

/-- Every result is a member of the original index. -/
theorem result_in_index (index : SemanticIndex) (q : Query)
    (e : IndexEntry) (h : e ∈ searchResults index q) :
    e ∈ index := by
  simp [searchResults] at h
  exact h.1

-- ════════════════════════════════════════════════════════════════════
-- § 6  Retrieval Completeness
-- ════════════════════════════════════════════════════════════════════

/-- Every entry in the index that matches the query appears in the
    results. (Theorem 6.1: Retrieval Completeness.) -/
theorem retrieval_complete (index : SemanticIndex) (q : Query)
    (e : IndexEntry) (hmem : e ∈ index) (hmatch : matchesQuery e q = true) :
    e ∈ searchResults index q := by
  simp [searchResults]
  exact ⟨hmem, hmatch⟩

-- ════════════════════════════════════════════════════════════════════
-- § 7  Score Computation
-- ════════════════════════════════════════════════════════════════════

/-- Count how many query tags an entry satisfies. -/
def matchCount (e : IndexEntry) (tags : List PropTag) : Nat :=
  (tags.filter (fun t => e.hasTag t)).length

/-- The match count never exceeds the number of query tags. -/
theorem matchCount_le_tags (e : IndexEntry) (tags : List PropTag) :
    matchCount e tags ≤ tags.length := by
  simp [matchCount]
  exact List.length_filter_le _ _

/-- An entry with all tags matched has matchCount equal to query length. -/
theorem full_match_count (e : IndexEntry) (tags : List PropTag)
    (h : satisfiesAllTags e tags = true) :
    matchCount e tags = tags.length := by
  unfold matchCount
  have : List.filter (fun t => e.hasTag t) tags = tags := by
    rw [List.filter_eq_self]
    simp [satisfiesAllTags] at h
    exact h
  rw [this]

-- ════════════════════════════════════════════════════════════════════
-- § 8  Empty Query Properties
-- ════════════════════════════════════════════════════════════════════

/-- An empty query (no required tags, lowest trust) matches any entry. -/
theorem empty_query_matches (e : IndexEntry) :
    matchesQuery e { requiredTags := [], minTrust := .low } = true := by
  simp [matchesQuery, satisfiesAllTags, meetsTrust, TrustLevel.toNat]

/-- An empty query returns the entire index. -/
theorem empty_query_returns_all (index : SemanticIndex) :
    searchResults index { requiredTags := [], minTrust := .low } = index := by
  unfold searchResults
  rw [List.filter_eq_self]
  intro e _
  exact empty_query_matches e

-- ════════════════════════════════════════════════════════════════════
-- § 9  Search Result Properties
-- ════════════════════════════════════════════════════════════════════

/-- Search results never exceed the index size. -/
theorem results_le_index (index : SemanticIndex) (q : Query) :
    (searchResults index q).length ≤ index.length := by
  simp [searchResults]
  exact List.length_filter_le _ _

/-- Empty index yields no results. -/
theorem empty_index_no_results (q : Query) :
    searchResults [] q = [] := by rfl

-- ════════════════════════════════════════════════════════════════════
-- § 10  Master Theorem
-- ════════════════════════════════════════════════════════════════════

/-- Master theorem packaging the principal results of Paper 57. -/
theorem semanticSearchCorrectness :
    -- (a) Soundness: results match the query.
    (∀ (idx : SemanticIndex) (q : Query) (e : IndexEntry),
      e ∈ searchResults idx q → matchesQuery e q = true) ∧
    -- (b) Completeness: matching entries are returned.
    (∀ (idx : SemanticIndex) (q : Query) (e : IndexEntry),
      e ∈ idx → matchesQuery e q = true → e ∈ searchResults idx q) ∧
    -- (c) Results bounded by index size.
    (∀ (idx : SemanticIndex) (q : Query),
      (searchResults idx q).length ≤ idx.length) ∧
    -- (d) Empty index gives empty results.
    (∀ (q : Query), searchResults [] q = []) :=
  ⟨retrieval_sound, retrieval_complete, results_le_index, empty_index_no_results⟩

end JudgmentGeometry.Paper57
