'''Atajo EXCLUSIVO para ensayos locales de expedientes etiquetados [PRUEBA].

No es un procedimiento oficial ni genera actas, evaluaciones ni firmas.
La API exige rol administrador_rsu, APP_ENV local/test, opt-in explícito,
y prefijo [PRUEBA] en el nombre del expediente.
'''
from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.db.mysql import fetch_one, get_connection
from app.schemas.auth import AuthUser

router = APIRouter(prefix="/herramientas-prueba", tags=["Simulación local RSU"])

class TestAdvanceIn(BaseModel):
    motivo: str = Field(min_length=10, max_length=400)


def _check_enabled(user: AuthUser) -> None:
    settings = get_settings()
    if str(user.rol_sistema or '').strip().lower() != 'administrador_rsu':
        raise HTTPException(403, 'Solo un administrador_rsu autorizado puede ejecutar simulaciones.')
    if not settings.rsu_enable_test_advance or settings.app_env.lower() not in {'local', 'test', 'testing', 'development'}:
        raise HTTPException(403, 'Las simulaciones están desactivadas en este servidor.')


@router.get('/estado')
def status(user: AuthUser = Depends(get_current_user)):
    settings = get_settings()
    enabled = bool(
        str(user.rol_sistema or '').strip().lower() == 'administrador_rsu'
        and settings.rsu_enable_test_advance
        and settings.app_env.lower() in {'local', 'test', 'testing', 'development'}
    )
    return {'habilitado': enabled, 'solo_expedientes_prueba': True,
            'mensaje': 'Atajos de simulación solo para expedientes [PRUEBA]. No equivalen a aprobación.'}


@router.post('/expedientes/{modalidad}/{project_id}/habilitar-siguiente')
def advance(
    modalidad: Literal['projects', 'activities', 'volunteers'],
    project_id: str,
    data: TestAdvanceIn,
    user: AuthUser = Depends(get_current_user),
):
    _check_enabled(user)
    with get_connection() as conn:
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute('SELECT proyecto_id,titulo FROM proyecto WHERE proyecto_id=%s FOR UPDATE', (project_id,))
            project = cur.fetchone()
            if not project:
                raise HTTPException(404, 'No se encontró el expediente de prueba.')
            if not str(project['titulo']).upper().startswith('[PRUEBA]'):
                raise HTTPException(403, 'Solo se permite avanzar expedientes cuyo título inicia con [PRUEBA].')
            workflow_table = {
                'projects': 'rsu_project_workflow',
                'activities': 'rsu_specific_activity_workflow',
                'volunteers': 'rsu_volunteer_workflow'
            }[modalidad]
            # Whitelisted SQL table values, never arbitrary inputs.
            cur.execute(f'SELECT * FROM {workflow_table} WHERE proyecto_id=%s FOR UPDATE', (project_id,))
            flow = cur.fetchone()
            if not flow:
                raise HTTPException(404, 'El expediente no corresponde a la modalidad indicada.')
            status = str(flow.get('estado_flujo') or '')
            stage = str(flow.get('etapa_actual') or '')
            note = ''
            if modalidad == 'projects':
                if stage in {'evaluacion_inicial', 'subsanacion_inicial', 'pendiente_acta', 'inicio_ejecucion'}:
                    cur.execute('''UPDATE rsu_project_workflow SET estado_flujo='en_ejecucion', etapa_actual='ejecucion',
                        anexo5_habilitado_manual=1, fecha_habilitacion_anexo5=NOW(), anexo5_habilitado_por=%s
                        WHERE proyecto_id=%s''', (user.persona_id, project_id))
                    note = 'SIMULACIÓN: ejecución y Anexo 5 habilitados sin generar aprobaciones oficiales'
                elif status in {'en_ejecucion', 'monitoreo'}:
                    cur.execute('SELECT anexo_numero FROM rsu_annex_submission WHERE proyecto_id=%s AND anexo_numero IN (5,6) AND es_actual=1', (project_id,))
                    presented = {int(r['anexo_numero']) for r in cur.fetchall()}
                    if 5 not in presented:
                        cur.execute('''UPDATE rsu_project_workflow SET anexo5_habilitado_manual=1,
                            fecha_habilitacion_anexo5=NOW(),anexo5_habilitado_por=%s WHERE proyecto_id=%s''', (user.persona_id, project_id))
                        note = 'SIMULACIÓN: Anexo 5 habilitado'
                    elif 6 not in presented:
                        cur.execute('''UPDATE rsu_project_workflow SET anexo6_habilitado_manual=1,
                            fecha_habilitacion_anexo6=NOW(),anexo6_habilitado_por=%s WHERE proyecto_id=%s''', (user.persona_id, project_id))
                        note = 'SIMULACIÓN: Anexo 6 habilitado'
                    else:
                        raise HTTPException(409, 'Los informes ya están presentados. Corresponde la revisión real.')
                else:
                    raise HTTPException(409, 'No existe una etapa simulable a partir del estado actual.')
            elif modalidad == 'activities':
                if stage not in {'evaluacion_inicial', 'subsanacion_inicial', 'lista_ejecucion'}:
                    raise HTTPException(409, 'La actividad no está en una etapa compatible con el atajo de pruebas.')
                cur.execute('''UPDATE rsu_specific_activity_workflow
                    SET estado_flujo='actividad_en_ejecucion',etapa_actual='ejecucion',fecha_inicio_ejecucion=CURDATE(),
                        fecha_limite_subsanacion=NULL WHERE proyecto_id=%s''', (project_id,))
                note = 'SIMULACIÓN: ejecución habilitada para presentar Anexo 10'
            elif modalidad == 'volunteers':
                if stage not in {'preparacion', 'evaluacion_inicial', 'subsanacion_inicial', 'lista_ejecucion'}:
                    raise HTTPException(409, 'El voluntariado no está en una etapa compatible con el atajo de pruebas.')
                cur.execute('''UPDATE rsu_volunteer_workflow
                    SET estado_flujo='voluntariado_en_ejecucion_interno',etapa_actual='ejecucion',modalidad='interno',
                        fecha_inicio_ejecucion=CURDATE(),fecha_limite_subsanacion=NULL WHERE proyecto_id=%s''', (project_id,))
                note = 'SIMULACIÓN: ejecución habilitada para presentar Anexo 13'
            # Audit explicit administrative simulation. Never emits official acts or certificates.
            from uuid import uuid4
            cur.execute('''INSERT INTO rsu_audit_log
                (audit_id,accion,modulo,entidad,recurso_id,metodo_http,ruta,detalle,estado_http,
                 usuario_id,persona_id,usuario_responsable,username,rol_sistema,fecha_hora)
                 VALUES (%s,'SIMULACIÓN','Pruebas RSU','Expediente',%s,'POST',%s,%s,200,%s,%s,%s,%s,%s,NOW(6))''',
                (str(uuid4()),project_id, f'/herramientas-prueba/expedientes/{modalidad}/{project_id}/habilitar-siguiente',
                 f'{note}. Estado anterior {stage}/{status}. Motivo: {data.motivo}'[:500],user.usuario_id,
                 user.persona_id,user.nombre_completo,user.username,user.rol_sistema))
            conn.commit()
            return {'ok': True, 'simulacion': True, 'modalidad': modalidad,
                    'expediente': project['titulo'], 'detalle': note,
                    'advertencia': 'No se emitieron aprobaciones ni documentos oficiales. Solo para pruebas.'}
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
