import Init
theorem increment_correct (n : Nat) : (let result := n + 1; result) = n + 1 := by
  /- sorry /- admit -/ unsafe -/
  rfl
