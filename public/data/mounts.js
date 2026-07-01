window.SIO_MOUNTS = {
  "Electric Scooter": { maxLevel: 10, dmgPerLevel: 0.003, costCurve: function(l){ return (l + 1) * 9; } },
  "Tech Hoverboard": { maxLevel: 10, dmgPerLevel: 0.004, costCurve: function(l){ return (l + 1) * 9; } },
  "Doomsteed": { maxLevel: 10, dmgPerLevel: 0.007, costCurve: function(l){ return (l + 1) * 9; } },
  "Mount Core": { maxLevel: 10, dmgPerLevel: 0, costCurve: function(l){ return (l + 1) * 9; } }
};
