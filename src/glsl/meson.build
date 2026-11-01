# shaderc
shaderc = dependency('shaderc', version: '>=2019.1', required: get_option('shaderc'))
components.set('shaderc', shaderc.found())
if shaderc.found()
  build_deps += shaderc
  sources += 'glsl/spirv_shaderc.c'

  # Version check for shaderc is not possible because everything after v2023.8
  # uses this version due to a malformed version line.
  # See https://github.com/google/shaderc/issues/1496
  if shaderc.type_name() == 'internal'
    conf_internal.set('PL_HAVE_SHADERC_VK_1_4', true)
    conf_internal.set('PL_HAVE_SHADERC_VK_1_3', true)
  else
    conf_internal.set('PL_HAVE_SHADERC_VK_1_4',
      cc.has_header_symbol('shaderc/shaderc.h', 'shaderc_env_version_vulkan_1_4', dependencies: shaderc))
    conf_internal.set('PL_HAVE_SHADERC_VK_1_3',
      cc.has_header_symbol('shaderc/shaderc.h', 'shaderc_env_version_vulkan_1_3', dependencies: shaderc))
  endif
endif

# glslang
glslang = disabler()
glslang_req = get_option('glslang')
if glslang_req.auto() and shaderc.found()

  # we only need one or the other, and shaderc is preferred
  message('Skipping `glslang` because `shaderc` is available')

elif not glslang_req.disabled()

  # the pkg-config package glslang provides glslang, SPIRV and
  # glslang-default-resource-limits as one library
  glslang = dependency('glslang', method: 'pkg-config', required: glslang_req)

endif

components.set('glslang', glslang.found())
if glslang.found()
  build_deps += glslang
  sources += [
    'glsl/glslang.cc',
    'glsl/glslang_resources.c',
    'glsl/spirv_glslang.c',
  ]
endif
