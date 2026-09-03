// fbx_writer — konwersja formatu posredniego LZMESH -> FBX (binary).
// Podejscie i kod przeniesione z Brutgen export/src/fbx_export.cpp:
// Autodesk FBX SDK, FbxLODGroup z progami w cm, FbxSystemUnit::cm,
// natywny (binarny) writer.
//
// Uzycie: fbx_writer.exe <input.lzmesh> <output.fbx> [--embed]
//   --embed: tekstury wbudowane w plik FBX (EXP_FBX_EMBEDDED)
//
// Format LZMESH v1/v2 (little-endian):
//   char[8]  magic = "LZMESH1\0" | "LZMESH2\0"
//   u32 nameLen; char name[]           nazwa assetu (np. SM_Crate)
//   u8  yUp                            1 = Y-up, 0 = Z-up (3ds Max)
//   u8  flags                          bit 0: flat — bez node'a FbxLODGroup,
//                                      dzieci *_LOD0..N pod zwyklym nodem
//                                      (naming Unity); v1 pisal tu 0
//   u16 pad
//   f32 scale                          mnoznik pozycji (m -> cm = 100)
//   u32 numMaterials
//     per mat: u32 len; char name[]
//              v1: 4x (u32 len; char path[])  basecolor/normal/orm/emissive
//              v2: 5x (u32 len; char path[])  + opacity ("" = brak)
//                  f32 opacityFactor          1.0 = nieprzezroczysty
//   u32 numLods
//   u32 numThresholds; f32 thresholds[]    progi LODGroup w cm
//   per LOD:
//     u32 vertCount; u32 triCount
//     f32 pos[v*3]; f32 nrm[v*3]; f32 uv[v*2]
//     u32 idx[t*3]; i32 triMat[t]

#include <fbxsdk.h>
#include <cstdio>
#include <cstdint>
#include <string>
#include <vector>

namespace {

struct Material {
    std::string name;
    std::string texBaseColor, texNormal, texOrm, texEmissive, texOpacity;
    float opacityFactor = 1.f;
};

struct LodMesh {
    std::vector<float> pos, nrm, uv;
    std::vector<uint32_t> idx;
    std::vector<int32_t> triMat;
};

struct Asset {
    std::string name;
    bool yUp = true;
    bool flatLods = false;   // bez FbxLODGroup (naming Unity)
    float scale = 100.f;
    std::vector<Material> materials;
    std::vector<LodMesh> lods;
    std::vector<float> thresholds;
};

bool readAll(FILE* f, void* dst, size_t n) { return std::fread(dst, 1, n, f) == n; }

bool readStr(FILE* f, std::string& s) {
    uint32_t len = 0;
    if (!readAll(f, &len, 4) || len > 1u << 20) return false;
    s.resize(len);
    return len == 0 || readAll(f, s.data(), len);
}

bool loadLzmesh(const char* path, Asset& a, std::string& err) {
    FILE* f = std::fopen(path, "rb");
    if (!f) { err = "nie mozna otworzyc pliku wejsciowego"; return false; }
    char magic[8] = {};
    if (!readAll(f, magic, 8) || std::string(magic, 6) != "LZMESH" ||
        (magic[6] != '1' && magic[6] != '2')) {
        err = "zly naglowek LZMESH"; std::fclose(f); return false;
    }
    const int version = magic[6] - '0';
    bool ok = readStr(f, a.name);
    uint8_t yUp = 1, flags = 0; uint16_t pad = 0;
    ok = ok && readAll(f, &yUp, 1) && readAll(f, &flags, 1) && readAll(f, &pad, 2)
            && readAll(f, &a.scale, 4);
    a.yUp = yUp != 0;
    a.flatLods = (flags & 1) != 0;
    uint32_t numMat = 0;
    ok = ok && readAll(f, &numMat, 4);
    for (uint32_t i = 0; ok && i < numMat; i++) {
        Material m;
        ok = readStr(f, m.name) && readStr(f, m.texBaseColor) &&
             readStr(f, m.texNormal) && readStr(f, m.texOrm) &&
             readStr(f, m.texEmissive);
        if (ok && version >= 2)
            ok = readStr(f, m.texOpacity) && readAll(f, &m.opacityFactor, 4);
        a.materials.push_back(std::move(m));
    }
    uint32_t numLods = 0, numThr = 0;
    ok = ok && readAll(f, &numLods, 4) && readAll(f, &numThr, 4);
    if (ok && numThr) {
        a.thresholds.resize(numThr);
        ok = readAll(f, a.thresholds.data(), numThr * 4);
    }
    for (uint32_t l = 0; ok && l < numLods; l++) {
        uint32_t vc = 0, tc = 0;
        ok = readAll(f, &vc, 4) && readAll(f, &tc, 4);
        if (!ok) break;
        LodMesh m;
        m.pos.resize(size_t(vc) * 3); m.nrm.resize(size_t(vc) * 3);
        m.uv.resize(size_t(vc) * 2);
        m.idx.resize(size_t(tc) * 3); m.triMat.resize(tc);
        ok = readAll(f, m.pos.data(), m.pos.size() * 4) &&
             readAll(f, m.nrm.data(), m.nrm.size() * 4) &&
             readAll(f, m.uv.data(), m.uv.size() * 4) &&
             readAll(f, m.idx.data(), m.idx.size() * 4) &&
             readAll(f, m.triMat.data(), m.triMat.size() * 4);
        a.lods.push_back(std::move(m));
    }
    std::fclose(f);
    if (!ok) err = "uszkodzony plik LZMESH (za krotki?)";
    if (ok && a.materials.empty()) { err = "brak materialow"; ok = false; }
    if (ok && a.lods.empty()) { err = "brak LOD-ow"; ok = false; }
    return ok;
}

// tekstura pliku podpieta pod property materialu (sciezka wzgledna obok FBX)
void attachTex(FbxScene* scene, FbxSurfacePhong* mat, const char* prop,
               const std::string& path) {
    if (path.empty()) return;
    FbxFileTexture* tex = FbxFileTexture::Create(scene, path.c_str());
    tex->SetFileName(path.c_str());
    tex->SetTextureUse(FbxTexture::eStandard);
    tex->SetMappingType(FbxTexture::eUV);
    tex->UVSet.Set("UVMap");
    FbxProperty p = mat->FindProperty(prop);
    if (p.IsValid()) p.ConnectSrcObject(tex);
}

// odpowiednik meshToFbx z Brutgena — eByControlPoint normal/uv,
// material per polygon (eIndexToDirect)
FbxMesh* meshToFbx(FbxScene* scene, const LodMesh& m, const char* name,
                   const std::vector<FbxSurfaceMaterial*>& mats, FbxNode* node,
                   float scale) {
    size_t vc = m.pos.size() / 3;
    FbxMesh* fm = FbxMesh::Create(scene, name);
    fm->InitControlPoints(int(vc));
    FbxVector4* cp = fm->GetControlPoints();
    for (size_t i = 0; i < vc; i++)
        cp[i] = FbxVector4(m.pos[i * 3] * scale, m.pos[i * 3 + 1] * scale,
                           m.pos[i * 3 + 2] * scale);
    auto* eNrm = fm->CreateElementNormal();
    eNrm->SetMappingMode(FbxGeometryElement::eByControlPoint);
    eNrm->SetReferenceMode(FbxGeometryElement::eDirect);
    auto* eUv = fm->CreateElementUV("UVMap");
    eUv->SetMappingMode(FbxGeometryElement::eByControlPoint);
    eUv->SetReferenceMode(FbxGeometryElement::eDirect);
    for (size_t i = 0; i < vc; i++) {
        eNrm->GetDirectArray().Add(FbxVector4(m.nrm[i * 3], m.nrm[i * 3 + 1],
                                              m.nrm[i * 3 + 2]));
        eUv->GetDirectArray().Add(FbxVector2(m.uv[i * 2], m.uv[i * 2 + 1]));
    }
    auto* eMat = fm->CreateElementMaterial();
    eMat->SetMappingMode(FbxGeometryElement::eByPolygon);
    eMat->SetReferenceMode(FbxGeometryElement::eIndexToDirect);
    for (FbxSurfaceMaterial* mt : mats) node->AddMaterial(mt);
    size_t tc = m.idx.size() / 3;
    for (size_t t = 0; t < tc; t++) {
        int mi = t < m.triMat.size() ? m.triMat[t] : 0;
        if (mi < 0 || mi >= int(mats.size())) mi = 0;
        fm->BeginPolygon(mi);
        fm->AddPolygon(int(m.idx[t * 3]));
        fm->AddPolygon(int(m.idx[t * 3 + 1]));
        fm->AddPolygon(int(m.idx[t * 3 + 2]));
        fm->EndPolygon();
    }
    return fm;
}

} // namespace

int main(int argc, char** argv) {
    if (argc < 3) {
        std::fprintf(stderr, "uzycie: fbx_writer <input.lzmesh> <output.fbx>\n");
        return 2;
    }
    Asset a;
    std::string err;
    if (!loadLzmesh(argv[1], a, err)) {
        std::fprintf(stderr, "LZMESH: %s\n", err.c_str());
        return 1;
    }

    bool embed = argc > 3 && std::string(argv[3]) == "--embed";

    FbxManager* mgr = FbxManager::Create();
    FbxIOSettings* ios = FbxIOSettings::Create(mgr, IOSROOT);
    ios->SetBoolProp(EXP_FBX_EMBEDDED, embed);
    mgr->SetIOSettings(ios);
    FbxScene* scene = FbxScene::Create(mgr, "lodziarz");
    FbxSystemUnit::cm.ConvertScene(scene);
    if (!a.yUp)
        FbxAxisSystem::Max.ConvertScene(scene); // Z-up
    else
        FbxAxisSystem(FbxAxisSystem::eYAxis, FbxAxisSystem::eParityOdd,
                      FbxAxisSystem::eRightHanded).ConvertScene(scene);

    std::vector<FbxSurfaceMaterial*> mats;
    for (const Material& m : a.materials) {
        FbxSurfacePhong* p = FbxSurfacePhong::Create(scene, m.name.c_str());
        p->Diffuse.Set(FbxDouble3(0.8, 0.8, 0.8));
        p->Specular.Set(FbxDouble3(0.0, 0.0, 0.0));
        attachTex(scene, p, FbxSurfaceMaterial::sDiffuse, m.texBaseColor);
        attachTex(scene, p, FbxSurfaceMaterial::sNormalMap, m.texNormal);
        attachTex(scene, p, FbxSurfaceMaterial::sEmissive, m.texEmissive);
        // przezroczystosc: mapa opacity (bialy = nieprzezroczysty) idzie do
        // TransparentColor (konwencja 3ds Max/Maya/UE); bez mapy sam factor
        if (!m.texOpacity.empty()) {
            attachTex(scene, p, FbxSurfaceMaterial::sTransparentColor,
                      m.texOpacity);
            p->TransparencyFactor.Set(1.0);   // rozstrzyga pixel mapy
        } else if (m.opacityFactor < 1.f) {
            p->TransparentColor.Set(FbxDouble3(1.0, 1.0, 1.0));
            p->TransparencyFactor.Set(1.0 - double(m.opacityFactor));
        }
        mats.push_back(p);
    }

    if (a.lods.size() > 1) {
        // node-grupa + dzieci <name>_LOD<i>; atrybut FbxLODGroup tylko gdy
        // nie flat — Unity buduje LODGroup z nazw, a FbxLODGroup ignoruje
        FbxNode* lodGroupNode = FbxNode::Create(scene, a.name.c_str());
        if (!a.flatLods) {
            FbxLODGroup* lodGroup = FbxLODGroup::Create(
                scene, (a.name + "_LODGroup").c_str());
            lodGroup->ThresholdsUsedAsPercentage.Set(false);
            for (size_t i = 0; i + 1 < a.lods.size(); i++) {
                float thr = i < a.thresholds.size() ? a.thresholds[i]
                                                    : float(2000.0 * (i + 1));
                FbxDistance d(thr, "cm");
                lodGroup->AddThreshold(d);
            }
            lodGroupNode->SetNodeAttribute(lodGroup);
        }
        for (size_t lod = 0; lod < a.lods.size(); lod++) {
            char name[256];
            std::snprintf(name, sizeof(name), "%s_LOD%zu", a.name.c_str(), lod);
            FbxNode* node = FbxNode::Create(scene, name);
            node->SetNodeAttribute(
                meshToFbx(scene, a.lods[lod], name, mats, node, a.scale));
            lodGroupNode->AddChild(node);
        }
        scene->GetRootNode()->AddChild(lodGroupNode);
    } else {
        FbxNode* node = FbxNode::Create(scene, a.name.c_str());
        node->SetNodeAttribute(
            meshToFbx(scene, a.lods[0], a.name.c_str(), mats, node, a.scale));
        scene->GetRootNode()->AddChild(node);
    }

    FbxExporter* exp = FbxExporter::Create(mgr, "");
    int fmt = mgr->GetIOPluginRegistry()->GetNativeWriterFormat(); // binary
    if (!exp->Initialize(argv[2], fmt, mgr->GetIOSettings())) {
        std::fprintf(stderr, "FBX init: %s\n", exp->GetStatus().GetErrorString());
        mgr->Destroy();
        return 1;
    }
    bool ok = exp->Export(scene);
    if (!ok) std::fprintf(stderr, "FBX export: %s\n",
                          exp->GetStatus().GetErrorString());
    exp->Destroy();
    mgr->Destroy();
    return ok ? 0 : 1;
}
