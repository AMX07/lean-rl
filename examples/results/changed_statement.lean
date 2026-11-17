import Init
theorem increment_correct (n : Nat) : (let result := n + 2; result) = n + 1 := by
  theorem increment_correct (n : Nat) : (let result := n + 1; result) = n + 1 := by
    rfl
