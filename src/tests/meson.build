gpu_tests = [
  'vulkan.c', 'opengl_surfaceless.c', 'd3d11.c', 'metal.c', 'dummy.c'
]

ts = []

foreach t : tests
  deps = [tdep_static]
  if t == 'opengl_surfaceless.c'
    deps += glad_dep
  endif
  if t == 'vulkan.c'
    deps += vulkan_headers
  endif
  sources = []
  if gpu_tests.contains(t)
    sources += 'gpu_tests.c'
  endif
  env = {}
  if t == 'metal.c'
    sources += ['metal_pass.c', 'metal_interop.c']
    deps += dependency('appleframeworks',
                       modules: ['CoreFoundation', 'CoreVideo', 'IOSurface'])
    if host_machine.subsystem() == 'ios'
      sources += 'metal_app.m'
      deps += dependency('appleframeworks', modules: ['UIKit'])
    else
      sources += 'metal_main.c'
    endif
    env = { 'MTL_DEBUG_LAYER': '1' }
  endif
  ts += { 'test': t,
          'sources': sources,
          'deps': deps,
          'env': env }
endforeach

dav1d = dependency('dav1d', required: false)
if dav1d.found()
  ts += { 'test': 'dav1d.c', 'deps': [dav1d, tdep_shared] }
endif

ts += { 'test': 'libav.c', 'deps': [lavu, tdep_shared] }

foreach t : ts
  e = executable('test.' + t['test'], [t['test']] + t.get('sources', []),
      objects: t.get('objects', []),
      c_args: [ '-Wno-unused-function' ],
      dependencies: t.get('deps', []),
      link_args: link_args,
      link_depends: link_depends,
  )

  test(t['test'], e, timeout: 120, env: t.get('env', {}))
endforeach

subdir('include')
