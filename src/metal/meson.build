metal = get_option('metal')
metal_dep = dependency('appleframeworks', modules: ['Metal', 'Foundation'],
                       required: metal)
metal = metal.require(metal_dep.found(),
                      error_message: 'Metal requires the Metal framework')
spirv_cross_dep = dependency('spirv-cross-c', method: 'pkg-config', required: metal)
metal = metal.require(spirv_cross_dep.found(),
                      error_message: 'Metal requires SPIRV-Cross')
metal = metal.require(components.get('glslang', false),
                      error_message: 'Metal requires glslang')
metal = metal.require(add_languages('objc', native: false, required: metal),
                      error_message: 'Metal requires an Objective-C compiler')

components.set('metal', metal.allowed())
if metal.allowed()
  add_project_arguments(build_opts + c_opts + ['-fobjc-arc'], language: 'objc')
  build_deps += [metal_dep, spirv_cross_dep]
  conf_internal.set_quoted('PL_METAL_COMPILER', 'glslang @0@ @1@, SPIRV-Cross @2@ @3@'.format(
    glslang.get_variable(pkgconfig: 'tag'),
    glslang.get_variable(pkgconfig: 'commit'),
    spirv_cross_dep.get_variable(pkgconfig: 'tag'),
    spirv_cross_dep.get_variable(pkgconfig: 'commit'),
  ))
  tests += 'metal.c'
  sources += [
    'metal/context.m',
    'metal/command.m',
    'metal/formats.m',
    'metal/gpu.m',
    'metal/gpu_buf.m',
    'metal/gpu_pass.m',
    'metal/gpu_tex.m',
    'metal/msl.m',
  ]
else
  sources += 'metal/stubs.c'
endif
