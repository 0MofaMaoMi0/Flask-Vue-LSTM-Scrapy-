export function assistantMovieTarget(movieId:number,surface:'explore'|'floating',returnTo:string){
 return {path:'/movies',query:{from:surface==='explore'?'ai':'floating',return_to:returnTo},hash:'#movie/'+movieId}
}
export function assistantReturnTarget(from:unknown,target:unknown){
 return typeof target==='string'&&/^\/(?:home|ai|movies|recommendations|insights|admin)(?:[?#]|$)/.test(target)?target:from==='ai'?'/ai':'/movies'
}
