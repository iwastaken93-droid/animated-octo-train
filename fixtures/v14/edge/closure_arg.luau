local function app(f, x) return f(x) end return app(function(a) return a*2 end, 21)
