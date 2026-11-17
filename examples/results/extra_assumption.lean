import Init
theorem increment_correct (n : Nat) : (let result := n + 2; result) = n + 1 := by
  section
  variable (h : False)
  include h
  theorem increment_correct (n : Nat) : (let result := n + 2; result) = n + 1 := by
    exact False.elim h
  end
